"""Unit tests for grouping geometry and the commit/undo roundtrip."""

import os
import shutil
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panelizer import cluster, geometry, organize  # noqa: E402
from panelizer.headers import Frame  # noqa: E402

# Seestar-like frame parameters.
FOCAL, PIX, N1, N2 = 250.0, 2.9, 1080, 1920


def make_frame(ra, dec, path="x.fit"):
    return Frame(
        path=path, ra_deg=ra, dec_deg=dec, focal_mm=FOCAL, pix_um=PIX,
        naxis1=N1, naxis2=N2, exptime=10.0, object="T", date_obs="",
    )


def test_plate_scale_and_fov():
    assert geometry.plate_scale_arcsec(PIX, FOCAL) == pytest.approx(2.393, abs=1e-3)
    w, h = geometry.frame_fov(FOCAL, PIX, N1, N2)
    assert w * 60 == pytest.approx(43.1, abs=0.2)
    assert h * 60 == pytest.approx(76.6, abs=0.3)


def test_project_roundtrip():
    ra, dec = 57.1, 24.3
    x, y = geometry.project(ra, dec, 57.0, 24.2)
    r2, d2 = geometry.deproject(x, y, 57.0, 24.2)
    assert float(np.atleast_1d(r2)[0]) == pytest.approx(ra, abs=1e-6)
    assert float(np.atleast_1d(d2)[0]) == pytest.approx(dec, abs=1e-6)


def test_three_well_separated_clusters():
    """Three tight knots 1 degree apart -> exactly three panels."""
    rng = np.random.default_rng(0)
    frames = []
    centers = [(57.0, 24.0), (58.0, 24.0), (57.0, 25.0)]
    for cra, cdec in centers:
        for _ in range(10):
            frames.append(make_frame(cra + rng.normal(0, 0.002), cdec + rng.normal(0, 0.002)))
    panels = cluster.group(frames, FOCAL, PIX, pct=10)
    assert len(panels) == 3
    assert sum(p.n_frames for p in panels) == 30


def test_threshold_monotonicity():
    """More slack never yields more panels."""
    rng = np.random.default_rng(1)
    frames = [make_frame(57 + rng.normal(0, 0.05), 24 + rng.normal(0, 0.05)) for _ in range(200)]
    counts = [len(cluster.group(frames, FOCAL, PIX, pct=p)) for p in (2, 5, 10, 30, 80)]
    assert counts == sorted(counts, reverse=True)


def test_all_frames_assigned_once():
    rng = np.random.default_rng(2)
    frames = [make_frame(57 + rng.normal(0, 0.1), 24 + rng.normal(0, 0.1)) for _ in range(100)]
    panels = cluster.group(frames, FOCAL, PIX, pct=8)
    seen = [i for p in panels for i in p.frame_indices]
    assert sorted(seen) == list(range(100))


def test_commit_undo_roundtrip(tmp_path):
    """Create fake files, commit into panel dirs, then undo restores layout."""
    folder = tmp_path / "data"
    folder.mkdir()
    frames = []
    # two clusters far apart
    for i, (cra, cdec) in enumerate([(57.0, 24.0), (60.0, 24.0)]):
        for j in range(3):
            name = f"f_{i}_{j}.fit"
            (folder / name).write_text("dummy")
            frames.append(make_frame(cra + j * 0.001, cdec, path=str(folder / name)))

    panels = cluster.group(frames, FOCAL, PIX, pct=10)
    assert len(panels) == 2

    before = sorted(os.listdir(folder))
    result = organize.commit(str(folder), panels, frames)
    assert result["n_files"] == 6
    assert result["mode"] == "move"
    assert organize.has_manifest(str(folder))
    # originals no longer at top level; panel dirs exist
    assert any(d.startswith("panel_") for d in os.listdir(folder))

    organize.undo(str(folder))
    assert not organize.has_manifest(str(folder))
    after = sorted(f for f in os.listdir(folder) if f.endswith(".fit"))
    assert after == [f for f in before if f.endswith(".fit")]
    # panel dirs cleaned up
    assert not any(d.startswith("panel_") for d in os.listdir(folder))


def test_copy_into_separate_dest(tmp_path):
    """Copy mode duplicates files into a separate dest, leaving originals."""
    src = tmp_path / "src"
    dest = tmp_path / "out"
    src.mkdir()
    frames = []
    for j in range(4):
        name = f"c_{j}.fit"
        (src / name).write_text("dummy")
        frames.append(make_frame(57.0 + j * 0.001, 24.0, path=str(src / name)))

    panels = cluster.group(frames, FOCAL, PIX, pct=10)
    result = organize.commit(str(src), panels, frames, dest=str(dest), mode="copy")
    assert result["mode"] == "copy"
    assert result["n_files"] == 4
    # originals untouched
    assert sorted(f for f in os.listdir(src) if f.endswith(".fit")) == \
        ["c_0.fit", "c_1.fit", "c_2.fit", "c_3.fit"]
    # copies landed under dest/panel_*/
    assert organize.has_manifest(str(dest))
    copied = [f for _, _, fs in os.walk(dest) for f in fs if f.endswith(".fit")]
    assert len(copied) == 4

    # undo of a copy deletes the copies but keeps originals
    organize.undo(str(dest))
    assert not organize.has_manifest(str(dest))
    assert not any(d.startswith("panel_") for d in os.listdir(dest))
    assert len([f for f in os.listdir(src) if f.endswith(".fit")]) == 4


def test_commit_cancel_rolls_back(tmp_path):
    """Cancelling mid-commit restores the folder to its original state."""
    folder = tmp_path / "data"
    folder.mkdir()
    frames = []
    for j in range(8):
        name = f"k_{j}.fit"
        (folder / name).write_text("dummy")
        frames.append(make_frame(57.0 + j * 0.5, 24.0, path=str(folder / name)))
    panels = cluster.group(frames, FOCAL, PIX, pct=2)  # many small panels
    before = sorted(f for f in os.listdir(folder) if f.endswith(".fit"))

    calls = {"n": 0}

    def cancel_after_3():
        calls["n"] += 1
        return calls["n"] > 3  # let 3 files through, then cancel

    result = organize.commit(
        str(folder), panels, frames, mode="move", should_cancel=cancel_after_3
    )
    assert result["cancelled"] is True
    assert result["n_rolled_back"] == 3
    # folder restored: originals back, no manifest, no panel dirs
    assert not organize.has_manifest(str(folder))
    assert not any(d.startswith("panel_") for d in os.listdir(folder))
    assert sorted(f for f in os.listdir(folder) if f.endswith(".fit")) == before


def test_commit_aborts_if_manifest_exists(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / organize.MANIFEST_NAME).write_text("{}")
    frames = [make_frame(57.0, 24.0, path=str(folder / "a.fit"))]
    (folder / "a.fit").write_text("x")
    panels = cluster.group(frames, FOCAL, PIX, pct=10)
    with pytest.raises(FileExistsError):
        organize.commit(str(folder), panels, frames)

"""Dry-run summaries and the actual file reorganisation (move + undo)."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from .cluster import Panel
from .headers import Frame

MANIFEST_NAME = "panelizer_manifest.json"


def dry_run(panels: list[Panel]) -> dict:
    """Summarise the proposed grouping without touching any files."""
    return {
        "n_panels": len(panels),
        "n_frames": sum(p.n_frames for p in panels),
        "panels": [
            {
                "id": p.id,
                "n_frames": p.n_frames,
                "center_ra": round(p.center_ra, 5),
                "center_dec": round(p.center_dec, 5),
                "total_exp": round(p.total_exp, 1),
            }
            for p in panels
        ],
    }


def _panel_dirname(pid: int, n_panels: int) -> str:
    width = max(2, len(str(n_panels)))
    return f"panel_{pid:0{width}d}"


def _rollback(ops: list[dict], mode: str) -> None:
    """Undo a partially-completed commit (used when cancelled mid-way)."""
    panel_dirs = set()
    for entry in reversed(ops):
        src, dst = entry["from"], entry["to"]
        if os.path.exists(dst):
            if mode == "copy":
                os.remove(dst)
            else:
                os.makedirs(os.path.dirname(src), exist_ok=True)
                shutil.move(dst, src)
        panel_dirs.add(os.path.dirname(dst))
    for d in sorted(panel_dirs):
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)


def commit(
    folder: str,
    panels: list[Panel],
    frames: list[Frame],
    dest: str | None = None,
    mode: str = "move",
    progress=None,
    should_cancel=None,
) -> dict:
    """Move or copy each frame into ``dest/panel_NN/`` and write a manifest.

    ``dest`` defaults to the source ``folder`` (in-place). ``mode`` is ``"move"``
    or ``"copy"``. Aborts (raising) before touching anything if a manifest
    already exists in ``dest`` or any target path is occupied, so the operation
    is all-or-nothing. The manifest lives in ``dest`` and drives undo.
    """
    if mode not in ("move", "copy"):
        raise ValueError(f"mode must be 'move' or 'copy', not {mode!r}")
    dest = dest or folder
    os.makedirs(dest, exist_ok=True)

    manifest_path = os.path.join(dest, MANIFEST_NAME)
    if os.path.exists(manifest_path):
        raise FileExistsError(
            f"{MANIFEST_NAME} already exists in target — run undo before committing again."
        )

    # Plan everything first, validating, then execute.
    planned: list[tuple[str, str]] = []  # (src, dst)
    for p in panels:
        dirname = _panel_dirname(p.id, len(panels))
        dest_dir = os.path.join(dest, dirname)
        for idx in p.frame_indices:
            src = frames[idx].path
            dst = os.path.join(dest_dir, os.path.basename(src))
            if os.path.exists(dst):
                raise FileExistsError(f"target already exists: {dst}")
            planned.append((src, dst))

    op = shutil.move if mode == "move" else shutil.copy2
    total = len(planned)
    ops: list[dict] = []
    for i, (src, dst) in enumerate(planned, 1):
        if should_cancel and should_cancel():
            _rollback(ops, mode)  # leave the folder exactly as we found it
            return {
                "cancelled": True,
                "n_files": 0,
                "n_rolled_back": len(ops),
                "n_panels": len(panels),
                "mode": mode,
                "dest": os.path.abspath(dest),
            }
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        op(src, dst)
        ops.append({"from": src, "to": dst})
        if progress:
            progress(i, total)

    manifest = {
        "created": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "source": os.path.abspath(folder),
        "dest": os.path.abspath(dest),
        "n_panels": len(panels),
        "ops": ops,
    }
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    return {
        "n_panels": len(panels),
        "n_files": len(ops),
        "mode": mode,
        "dest": os.path.abspath(dest),
        "manifest": manifest_path,
    }


def has_manifest(folder: str) -> bool:
    return os.path.exists(os.path.join(folder, MANIFEST_NAME))


def undo(folder: str) -> dict:
    """Reverse a previous :func:`commit` using the manifest in ``folder``.

    A ``move`` commit is undone by moving files back to their original paths; a
    ``copy`` commit is undone by deleting the copies (originals are left alone).
    Empty ``panel_NN/`` dirs are then removed along with the manifest.
    """
    manifest_path = os.path.join(folder, MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"no {MANIFEST_NAME} in {folder}")

    with open(manifest_path) as fh:
        manifest = json.load(fh)

    mode = manifest.get("mode", "move")
    ops = manifest.get("ops", manifest.get("moves", []))  # tolerate old key
    restored = 0
    panel_dirs = set()
    for entry in reversed(ops):
        src, dst = entry["from"], entry["to"]
        if not os.path.exists(dst):
            panel_dirs.add(os.path.dirname(dst))
            continue
        if mode == "copy":
            os.remove(dst)  # delete the copy; leave the original
        else:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            shutil.move(dst, src)
        restored += 1
        panel_dirs.add(os.path.dirname(dst))

    for d in sorted(panel_dirs):
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)

    os.remove(manifest_path)
    return {"n_restored": restored, "mode": mode}

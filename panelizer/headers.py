"""Scan a folder of FITS files into lightweight :class:`Frame` records.

Only the primary header is read (no pixel data), so scanning 1500+ frames is
fast and memory-cheap.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Callable, Optional

from astropy.io import fits

# Header keyword fallbacks, in priority order.
_RA_KEYS = ("RA", "OBJCTRA", "CRVAL1")
_DEC_KEYS = ("DEC", "OBJCTDEC", "CRVAL2")
_FOCAL_KEYS = ("FOCALLEN", "FOCAL")
_PIX_KEYS = ("XPIXSZ", "PIXSIZE1", "PIXSIZE")
_EXP_KEYS = ("EXPTIME", "EXPOSURE", "EXP")


@dataclass
class Frame:
    path: str
    ra_deg: float
    dec_deg: float
    focal_mm: float
    pix_um: float
    naxis1: int
    naxis2: int
    exptime: float
    object: str
    date_obs: str

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def _first(header, keys):
    for k in keys:
        if k in header:
            return header[k]
    return None


def _coerce_deg(value):
    """RA/Dec are decimal degrees in this dataset; tolerate sexagesimal strings."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        return float(s)
    except ValueError:
        # "hh mm ss" / "dd mm ss" — interpret as degrees-of-arc style only if
        # clearly sexagesimal. Kept simple; this dataset uses decimal degrees.
        parts = s.replace(":", " ").split()
        if len(parts) == 3:
            sign = -1.0 if parts[0].startswith("-") else 1.0
            a, b, c = (abs(float(parts[0])), float(parts[1]), float(parts[2]))
            return sign * (a + b / 60.0 + c / 3600.0)
        raise


def iter_fits_paths(folder: str, recursive: bool = False):
    """Yield .fit/.fits paths (case-insensitive), sorted.

    When ``recursive=False`` (default) only the immediate folder is scanned,
    which avoids picking up already-committed panel_NN/ subfolders on a re-run.
    When ``recursive=True`` every subfolder is walked.
    """
    seen = set()
    patterns = ("*.fit", "*.fits", "*.FIT", "*.FITS")
    if recursive:
        for root, dirs, _ in os.walk(folder):
            dirs.sort()
            for pat in patterns:
                for p in glob.glob(os.path.join(root, pat)):
                    if p not in seen:
                        seen.add(p)
                        yield p
    else:
        for pat in patterns:
            for p in glob.glob(os.path.join(folder, pat)):
                if p not in seen:
                    seen.add(p)
                    yield p


def scan_folder(
    folder: str,
    progress: Optional[Callable[[int, int], None]] = None,
    recursive: bool = False,
) -> tuple[list[Frame], list[str]]:
    """Read headers from every FITS file in ``folder`` (and optionally subfolders).

    Returns ``(frames, skipped)`` where ``skipped`` lists basenames that lacked
    usable RA/Dec. ``progress(done, total)`` is called as scanning proceeds.
    """
    paths = sorted(iter_fits_paths(folder, recursive=recursive))
    total = len(paths)
    frames: list[Frame] = []
    skipped: list[str] = []

    for i, path in enumerate(paths, 1):
        try:
            header = fits.getheader(path, ext=0)
        except Exception:
            skipped.append(os.path.basename(path))
            if progress:
                progress(i, total)
            continue

        ra = _coerce_deg(_first(header, _RA_KEYS))
        dec = _coerce_deg(_first(header, _DEC_KEYS))
        if ra is None or dec is None:
            skipped.append(os.path.basename(path))
            if progress:
                progress(i, total)
            continue

        focal = _first(header, _FOCAL_KEYS)
        pix = _first(header, _PIX_KEYS)
        frames.append(
            Frame(
                path=path,
                ra_deg=float(ra) % 360.0,
                dec_deg=float(dec),
                focal_mm=float(focal) if focal else 0.0,
                pix_um=float(pix) if pix else 0.0,
                naxis1=int(header.get("NAXIS1", 0)),
                naxis2=int(header.get("NAXIS2", 0)),
                exptime=float(_first(header, _EXP_KEYS) or 0.0),
                object=str(header.get("OBJECT", "")).strip(),
                date_obs=str(header.get("DATE-OBS", "")).strip(),
            )
        )
        if progress:
            progress(i, total)

    return frames, skipped


def median_or(values, fallback: float) -> float:
    """Median of positive values, else ``fallback`` (used for form defaults)."""
    vals = sorted(v for v in values if v and v > 0)
    if not vals:
        return fallback
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0

"""Plate-scale / field-of-view math and tangent-plane projection.

All angles are in degrees unless suffixed otherwise. Footprints are drawn
axis-aligned to RA/Dec because the source headers carry no rotation/WCS info
(see README) — this is a deliberate approximation that is fine for planning
how mosaic panels tile and overlap.
"""

from __future__ import annotations

import math

import numpy as np

# Arcseconds per radian / 1000  ->  the classic 206.265 plate-scale constant
# scale["/px] = 206.265 * pixel_size[um] / focal_length[mm]
_PLATE_CONST = 206.264806


def plate_scale_arcsec(pix_um: float, focal_mm: float) -> float:
    """Plate scale in arcseconds per pixel."""
    if focal_mm <= 0:
        raise ValueError("focal length must be positive")
    return _PLATE_CONST * pix_um / focal_mm


def fov_deg(naxis: int, pix_um: float, focal_mm: float) -> float:
    """Field of view along one axis, in degrees."""
    return naxis * plate_scale_arcsec(pix_um, focal_mm) / 3600.0


def frame_fov(focal_mm: float, pix_um: float, naxis1: int, naxis2: int) -> tuple[float, float]:
    """Return (width_deg, height_deg) of a frame."""
    return (
        fov_deg(naxis1, pix_um, focal_mm),
        fov_deg(naxis2, pix_um, focal_mm),
    )


def project(ra, dec, ra0, dec0):
    """Gnomonic (tangent-plane) projection about (ra0, dec0).

    Returns (x_deg, y_deg) where x runs along increasing RA and y along
    increasing Dec. Accepts scalars or numpy arrays. Handles the RA cos(dec)
    term and RA wraparound implicitly via the trig identities.
    """
    ra = np.radians(np.asarray(ra, dtype=float))
    dec = np.radians(np.asarray(dec, dtype=float))
    ra0r = math.radians(ra0)
    dec0r = math.radians(dec0)

    cos_c = math.sin(dec0r) * np.sin(dec) + math.cos(dec0r) * np.cos(dec) * np.cos(ra - ra0r)
    # Guard against division blow-up at the antipode (not reachable for a mosaic).
    cos_c = np.where(np.abs(cos_c) < 1e-12, 1e-12, cos_c)

    x = np.cos(dec) * np.sin(ra - ra0r) / cos_c
    y = (math.cos(dec0r) * np.sin(dec) - math.sin(dec0r) * np.cos(dec) * np.cos(ra - ra0r)) / cos_c
    return np.degrees(x), np.degrees(y)


def deproject(x_deg, y_deg, ra0, dec0):
    """Inverse of :func:`project`: tangent-plane (deg) back to (ra, dec) deg."""
    x = np.radians(np.asarray(x_deg, dtype=float))
    y = np.radians(np.asarray(y_deg, dtype=float))
    ra0r = math.radians(ra0)
    dec0r = math.radians(dec0)

    rho = np.sqrt(x * x + y * y)
    c = np.arctan(rho)
    sin_c = np.sin(c)
    cos_c = np.cos(c)
    # rho == 0 -> the tangent point itself.
    safe_rho = np.where(rho == 0, 1.0, rho)

    dec = np.arcsin(cos_c * math.sin(dec0r) + (y * sin_c * math.cos(dec0r)) / safe_rho)
    ra = ra0r + np.arctan2(
        x * sin_c,
        rho * math.cos(dec0r) * cos_c - y * math.sin(dec0r) * sin_c,
    )
    ra = np.where(rho == 0, ra0r, ra)
    dec = np.where(rho == 0, dec0r, dec)
    return np.degrees(ra) % 360.0, np.degrees(dec)


def footprint_corners(center_ra, center_dec, w_deg, h_deg):
    """Four RA/Dec corners of an axis-aligned frame footprint.

    Built in the tangent plane centred on the frame so the corners stay
    correctly sized regardless of declination, then deprojected. Returned in
    order (TL, TR, BR, BL) suitable for a closed polygon.
    """
    hw, hh = w_deg / 2.0, h_deg / 2.0
    xs = np.array([-hw, hw, hw, -hw])
    ys = np.array([hh, hh, -hh, -hh])
    ra, dec = deproject(xs, ys, center_ra, center_dec)
    return list(zip(ra.tolist(), dec.tolist()))

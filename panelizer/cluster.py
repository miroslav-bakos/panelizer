"""Group frames into mosaic panels by proximity of their centres.

Single-linkage clustering (equivalent to DBSCAN with ``min_samples=1``): two
frames join the same panel if their centres are within ``linking_radius`` of
each other, transitively. Implemented with a uniform spatial grid plus a
union-find so it stays roughly O(n) for the ~1500-frame datasets here — no
scipy/sklearn dependency.

The threshold is expressed as a percentage of the frame field of view; the
reference dimension is the *smaller* of width/height (conservative — frames
must be quite close to merge).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import geometry
from .headers import Frame


@dataclass
class Panel:
    id: int
    frame_indices: list[int]
    center_ra: float
    center_dec: float
    n_frames: int
    total_exp: float
    bbox: tuple[float, float, float, float]  # ra_min, ra_max, dec_min, dec_max (deg)
    footprints: list[list[tuple[float, float]]] = field(default_factory=list)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        root = a
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[a] != root:  # path compression
            self.parent[a], a = root, self.parent[a]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def linking_radius_deg(focal_mm: float, pix_um: float, naxis1: int, naxis2: int, pct: float) -> float:
    """Proximity threshold (deg) for a given %-of-FOV setting."""
    w, h = geometry.frame_fov(focal_mm, pix_um, naxis1, naxis2)
    return (pct / 100.0) * min(w, h)


def _grid_pairs(xy: np.ndarray, radius: float):
    """Yield candidate index pairs within ``radius`` using a uniform grid.

    Each point only needs to be compared against points in its own and the 8
    neighbouring cells (cell size == radius).
    """
    if radius <= 0:
        return
    cells: dict[tuple[int, int], list[int]] = {}
    keys = np.floor(xy / radius).astype(np.int64)
    for i, (cx, cy) in enumerate(keys):
        cells.setdefault((int(cx), int(cy)), []).append(i)

    r2 = radius * radius
    for (cx, cy), members in cells.items():
        # Gather this cell + the 3x3 neighbourhood, but only "forward" cells to
        # avoid testing each unordered pair twice.
        neighbours: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbours.extend(cells.get((cx + dx, cy + dy), ()))
        for ii, a in enumerate(members):
            ax, ay = xy[a]
            for b in neighbours:
                if b <= a:
                    continue
                dx = xy[b, 0] - ax
                dy = xy[b, 1] - ay
                if dx * dx + dy * dy <= r2:
                    yield a, b


def group(frames: list[Frame], focal_mm: float, pix_um: float, pct: float) -> list[Panel]:
    """Cluster ``frames`` into panels. Returns panels sorted by descending size."""
    n = len(frames)
    if n == 0:
        return []

    ras = np.array([f.ra_deg for f in frames])
    decs = np.array([f.dec_deg for f in frames])
    # Tangent plane about the field median keeps Euclidean distance ~= angular
    # separation for the few-degree fields this tool targets.
    ra0 = float(np.median(ras))
    dec0 = float(np.median(decs))
    px, py = geometry.project(ras, decs, ra0, dec0)
    xy = np.column_stack([px, py])

    naxis1 = frames[0].naxis1 or 0
    naxis2 = frames[0].naxis2 or 0
    radius = linking_radius_deg(focal_mm, pix_um, naxis1, naxis2, pct)

    uf = _UnionFind(n)
    for a, b in _grid_pairs(xy, radius):
        uf.union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    ordered = sorted(groups.values(), key=len, reverse=True)
    w_deg, h_deg = geometry.frame_fov(focal_mm, pix_um, naxis1, naxis2)

    panels: list[Panel] = []
    for pid, members in enumerate(ordered, 1):
        m_ra = ras[members]
        m_dec = decs[members]
        # Average in tangent plane to dodge RA-wrap issues, then deproject.
        cx = float(np.mean(px[members]))
        cy = float(np.mean(py[members]))
        c_ra_arr, c_dec_arr = geometry.deproject(cx, cy, ra0, dec0)
        center_ra = float(np.atleast_1d(c_ra_arr)[0])
        center_dec = float(np.atleast_1d(c_dec_arr)[0])

        footprints = [
            geometry.footprint_corners(frames[i].ra_deg, frames[i].dec_deg, w_deg, h_deg)
            for i in members
        ]
        all_pts = np.array([pt for fp in footprints for pt in fp])
        bbox = (
            float(all_pts[:, 0].min()),
            float(all_pts[:, 0].max()),
            float(all_pts[:, 1].min()),
            float(all_pts[:, 1].max()),
        )
        panels.append(
            Panel(
                id=pid,
                frame_indices=members,
                center_ra=center_ra,
                center_dec=center_dec,
                n_frames=len(members),
                total_exp=float(sum(frames[i].exptime for i in members)),
                bbox=bbox,
                footprints=footprints,
            )
        )
    return panels

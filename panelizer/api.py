"""The object exposed to the web frontend via pywebview's ``js_api`` bridge.

Every public method returns JSON-serialisable plain dicts/lists so it can cross
the Python<->JS boundary. State (current folder, scanned frames, last grouping)
lives on the instance.
"""

from __future__ import annotations

import json
import threading

import numpy as np

from . import cluster, geometry, organize
from .headers import median_or, scan_folder

DEFAULT_FOCAL = 250.0
DEFAULT_PIX = 2.9


class Api:
    def __init__(self):
        self._window = None
        self.folder: str | None = None
        self.frames: list = []
        self.skipped: list[str] = []
        self.panels: list = []
        self.last_dest: str | None = None  # where the last commit wrote (for undo)
        self._cancel = threading.Event()   # set by cancel_commit, polled by commit
        self._lock = threading.Lock()

    def attach(self, window) -> None:
        self._window = window

    # ------------------------------------------------------------------ folder
    def _pick_folder(self) -> str | None:
        import webview

        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    def choose_folder(self) -> dict:
        """Pick the source folder to scan."""
        return {"folder": self._pick_folder()}

    def choose_target(self) -> dict:
        """Pick a destination folder for copy/move (separate from the source)."""
        return {"folder": self._pick_folder()}

    # -------------------------------------------------------------------- scan
    def scan(self, folder: str, recursive: bool = False) -> dict:
        """Read all FITS headers in ``folder`` and return form defaults."""

        def progress(done, total):
            if self._window is not None:
                self._window.evaluate_js(f"window.onScanProgress({done},{total})")

        frames, skipped = scan_folder(folder, progress=progress, recursive=recursive)
        with self._lock:
            self.folder = folder
            self.frames = frames
            self.skipped = skipped
            self.panels = []
            # If this folder holds a manifest, an in-place commit can be undone.
            self.last_dest = folder if organize.has_manifest(folder) else None

        focal = median_or((f.focal_mm for f in frames), DEFAULT_FOCAL)
        pix = median_or((f.pix_um for f in frames), DEFAULT_PIX)
        naxis1 = frames[0].naxis1 if frames else 1080
        naxis2 = frames[0].naxis2 if frames else 1920
        return {
            "folder": folder,
            "n_frames": len(frames),
            "n_skipped": len(skipped),
            "skipped": skipped[:50],
            "focal": round(focal, 3),
            "pix": round(pix, 4),
            "naxis1": naxis1,
            "naxis2": naxis2,
            "has_manifest": organize.has_manifest(folder),
        }

    # --------------------------------------------------------------- recompute
    def recompute(self, focal: float, pix: float, pct: float) -> dict:
        """Re-cluster with the given parameters; return panels + map geometry."""
        focal = float(focal)
        pix = float(pix)
        pct = float(pct)
        with self._lock:
            frames = self.frames
        if not frames:
            return {"n_panels": 0, "panels": [], "field": None}

        panels = cluster.group(frames, focal, pix, pct)
        with self._lock:
            self.panels = panels

        w_deg, h_deg = geometry.frame_fov(focal, pix, frames[0].naxis1, frames[0].naxis2)

        panel_dicts = []
        ra_lo = dec_lo = float("inf")
        ra_hi = dec_hi = float("-inf")
        for p in panels:
            ra_lo, ra_hi = min(ra_lo, p.bbox[0]), max(ra_hi, p.bbox[1])
            dec_lo, dec_hi = min(dec_lo, p.bbox[2]), max(dec_hi, p.bbox[3])
            panel_dicts.append(
                {
                    "id": p.id,
                    "n_frames": p.n_frames,
                    "center_ra": round(p.center_ra, 5),
                    "center_dec": round(p.center_dec, 5),
                    "total_exp": round(p.total_exp, 1),
                    "bbox": [round(v, 5) for v in p.bbox],
                    "footprints": [
                        [[round(c[0], 5), round(c[1], 5)] for c in fp] for fp in p.footprints
                    ],
                }
            )

        center_ra = float(np.median([f.ra_deg for f in frames]))
        center_dec = float(np.median([f.dec_deg for f in frames]))
        # FoV span (deg) for Aladin auto-fit; pad and never smaller than a frame.
        span = max(
            (ra_hi - ra_lo) * np.cos(np.radians(center_dec)),
            dec_hi - dec_lo,
            w_deg,
            h_deg,
        ) * 1.2

        return {
            "n_panels": len(panels),
            "panels": panel_dicts,
            "frame_fov": [round(w_deg, 5), round(h_deg, 5)],
            "field": {
                "center_ra": round(center_ra, 5),
                "center_dec": round(center_dec, 5),
                "fov_deg": round(span, 4),
            },
        }

    # ------------------------------------------------------------ commit / undo
    def commit(self, mode: str = "move", dest: str | None = None) -> dict:
        """Move or copy frames into ``dest`` (defaults to the source folder)."""
        with self._lock:
            folder, panels, frames = self.folder, self.panels, self.frames
        if not folder or not panels:
            return {"ok": False, "error": "Run a dry-run first."}
        dest = dest or folder

        # Throttle bridge calls to ~100 updates so evaluate_js round-trips don't
        # bottleneck the copy/move loop.
        total_planned = sum(p.n_frames for p in panels)
        step = max(1, total_planned // 100)

        def progress(done, total):
            if self._window is not None and (done % step == 0 or done == total):
                self._window.evaluate_js(f"window.onCommitProgress({done},{total})")

        self._cancel.clear()
        try:
            result = organize.commit(
                folder, panels, frames, dest=dest, mode=mode,
                progress=progress, should_cancel=self._cancel.is_set,
            )
            if result.get("cancelled"):
                return {"ok": True, **result}  # rolled back; nothing to undo
            with self._lock:
                self.last_dest = result["dest"]
            return {"ok": True, **result}
        except Exception as exc:  # surface to UI rather than crash the bridge
            return {"ok": False, "error": str(exc)}

    def cancel_commit(self) -> dict:
        """Request the in-progress commit to stop and roll back."""
        self._cancel.set()
        return {"ok": True}

    def undo(self) -> dict:
        with self._lock:
            dest = self.last_dest
        if not dest:
            return {"ok": False, "error": "Nothing to undo."}
        try:
            result = organize.undo(dest)
            with self._lock:
                self.last_dest = None
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def export_report(self) -> dict:
        """Write a panels.json mapping frame -> panel next to the data."""
        with self._lock:
            folder, panels, frames = self.folder, self.panels, self.frames
        if not folder or not panels:
            return {"ok": False, "error": "Nothing to export."}
        rows = []
        for p in panels:
            for idx in p.frame_indices:
                rows.append({"frame": frames[idx].name, "panel": p.id})
        import os

        path = os.path.join(folder, "panels.json")
        with open(path, "w") as fh:
            json.dump(rows, fh, indent=2)
        return {"ok": True, "path": path, "n_rows": len(rows)}

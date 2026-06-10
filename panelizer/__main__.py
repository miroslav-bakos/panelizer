"""Entry point: GUI window (default) or a headless ``--cli`` dry-run.

GUI:   python -m panelizer
CLI:   python -m panelizer --cli lights/ --focal 250 --pixel 2.9 --threshold 30
"""

from __future__ import annotations

import argparse
import os
import sys

# Linux/GTK workarounds — must be set before any webview import.
# On Wayland+NVIDIA, GTK WebKit needs XWayland and software compositing.
if sys.platform.startswith("linux"):
    os.environ["GDK_BACKEND"] = "x11"
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

from . import cluster, organize
from .api import DEFAULT_FOCAL, DEFAULT_PIX, Api
from .headers import median_or, scan_folder

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def run_cli(args) -> int:
    frames, skipped = scan_folder(args.folder, recursive=args.recursive)
    if not frames:
        print(f"No usable FITS frames in {args.folder!r} ({len(skipped)} skipped).")
        return 1

    focal = args.focal or median_or((f.focal_mm for f in frames), DEFAULT_FOCAL)
    pix = args.pixel or median_or((f.pix_um for f in frames), DEFAULT_PIX)

    from .geometry import frame_fov, plate_scale_arcsec

    w, h = frame_fov(focal, pix, frames[0].naxis1, frames[0].naxis2)
    radius = cluster.linking_radius_deg(focal, pix, frames[0].naxis1, frames[0].naxis2, args.threshold)
    panels = cluster.group(frames, focal, pix, args.threshold)
    summary = organize.dry_run(panels)

    print(f"Folder        : {args.folder}")
    print(f"Frames        : {len(frames)}  (skipped {len(skipped)})")
    print(f"Focal / pixel : {focal:g} mm / {pix:g} um  -> {plate_scale_arcsec(pix, focal):.3f}\"/px")
    print(f"Frame FOV     : {w*60:.1f}' x {h*60:.1f}'")
    print(f"Threshold     : {args.threshold:g}% of FOV  (link radius {radius*60:.2f}')")
    print(f"Panels        : {summary['n_panels']}")
    print()
    print(f"  {'panel':>5}  {'frames':>6}  {'center RA':>10}  {'center Dec':>10}  {'tot exp':>8}")
    for p in summary["panels"]:
        print(
            f"  {p['id']:>5}  {p['n_frames']:>6}  {p['center_ra']:>10.4f}  "
            f"{p['center_dec']:>10.4f}  {p['total_exp']:>8.1f}"
        )

    if args.commit:
        mode = "copy" if args.copy else "move"

        gerund = "copying" if mode == "copy" else "moving"

        def _progress(done, total):
            print(f"\r  {gerund} {done}/{total} files…", end="", flush=True)
            if done == total:
                print()

        result = organize.commit(
            args.folder, panels, frames, dest=args.dest or None, mode=mode, progress=_progress
        )
        verb = "Copied" if mode == "copy" else "Moved"
        print(f"\n{verb} {result['n_files']} files into {result['n_panels']} panels at {result['dest']}.")
        print(f"Manifest: {result['manifest']}")
    return 0


def run_gui() -> int:
    import webview

    api = Api()
    index = os.path.join(WEB_DIR, "index.html")
    window = webview.create_window(
        "Panelizer — Mosaic Panel Planner",
        index,
        js_api=api,
        width=1320,
        height=860,
        min_size=(1000, 640),
    )
    api.attach(window)
    # On Linux use GTK/WebKit explicitly; on macOS/Windows let pywebview auto-select.
    webview.start(gui="gtk" if sys.platform.startswith("linux") else None)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="panelizer", description=__doc__)
    parser.add_argument("--cli", action="store_true", help="run headless dry-run instead of the GUI")
    parser.add_argument("folder", nargs="?", help="folder of FITS files (CLI mode)")
    parser.add_argument("--focal", type=float, default=0.0, help="override focal length (mm)")
    parser.add_argument("--pixel", type=float, default=0.0, help="override pixel size (um)")
    parser.add_argument("--threshold", type=float, default=30.0, help="proximity, %% of frame FOV")
    parser.add_argument("--recursive", action="store_true", help="scan subfolders too")
    parser.add_argument("--commit", action="store_true", help="(CLI) actually move/copy files")
    parser.add_argument("--copy", action="store_true", help="copy instead of move (with --commit)")
    parser.add_argument("--dest", default="", help="destination folder (default: source folder)")
    args = parser.parse_args(argv)

    if args.cli or args.folder:
        if not args.folder:
            parser.error("CLI mode requires a folder argument")
        return run_cli(args)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())

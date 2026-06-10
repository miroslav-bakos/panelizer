# Panelizer

[![Tests](https://github.com/YOUR_USERNAME/panelizer/actions/workflows/test.yml/badge.svg)](https://github.com/YOUR_USERNAME/panelizer/actions/workflows/test.yml)

A lightweight desktop tool for turning a folder of scattered FITS sub-exposures
(e.g. a Seestar / smart-telescope mosaic) into organised **panels**, grouped by
how close their pointing centres are on the sky.

It can:

- read **RA/Dec**, focal length and pixel size straight from FITS headers,
- let you **override** focal length & pixel size (these drive plate scale / FOV),
- **group** frames into panels by a proximity threshold expressed as **% of the frame FOV**,
- do a **dry run** that just reports how many panels result — no files touched,
- **visualise** panels and their overlap over an Aladin Lite sky background, auto-fit to the field,
- on **commit**, move frames into `panel_NN/` subfolders (with one-click **Undo**).

## Install

This project targets the system Python on Arch/CachyOS, which blocks `pip` from
writing into it. Create a venv that *inherits* the system packages (so it can see
`astropy`, `numpy` and the GTK WebKit bindings pywebview needs) and add the one
missing dependency:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install pywebview pytest
```

> The GUI uses pywebview's **GTK WebKit** backend, which relies on the system
> `gi` / `WebKit2` bindings (already present here). The only pip-installed
> package is `pywebview` itself.

## Run

GUI:

```bash
.venv/bin/python -m panelizer
```

> On some Wayland sessions GTK WebKit fails with a "Protocol error dispatching to
> Wayland display". If that happens, route through XWayland:
> `GDK_BACKEND=x11 .venv/bin/python -m panelizer`.

1. **Choose folder…** — pick a directory of `.fit`/`.fits` files (tick *Include
   subfolders* to recurse). Focal length and pixel size are pre-filled from the headers.
2. Drag the **proximity threshold** slider; the panel count, table and sky map
   update live.
3. Pick **Move** or **Copy**, and optionally a **Target folder** (defaults to the
   source). **Commit** reorganises into `dest/panel_NN/` subfolders, showing a
   **progress bar with ETA**; **Cancel** stops it and rolls back any files
   already moved/copied. **Undo** reverses a completed commit — moving files
   back, or deleting the copies.
4. **Export panels.json** writes a `frame → panel` mapping without moving anything.

Headless dry-run (no GUI / WebKit needed — handy for scripting and CI):

```bash
.venv/bin/python -m panelizer --cli lights/ --focal 250 --pixel 2.9 --threshold 15
# move into panel_NN/ subfolders:
.venv/bin/python -m panelizer --cli lights/ --threshold 15 --commit
# copy (instead of move) into a separate destination:
.venv/bin/python -m panelizer --cli lights/ --threshold 15 --commit --copy --dest /path/to/out
```

## How grouping works

Each frame is reduced to its pointing centre. Frames are clustered by
**single-linkage** (DBSCAN with `min_samples=1`): two frames join the same panel
if their centres are within the link radius, transitively. The link radius is

```
radius = (threshold% / 100) × min(frame_width, frame_height)
```

so the threshold is resolution-independent. A uniform spatial grid + union-find
keeps it fast (~1500 frames cluster instantly).

Because a dense mosaic overlaps heavily, a *large* threshold will chain
neighbouring panels into one blob — that is expected. The useful range for a
tightly-stepped mosaic is the low end (a few %); the dry-run + visualisation are
there precisely so you can find the threshold that matches your capture pattern.

## Notes & limitations

- **Footprints are axis-aligned** to RA/Dec. These headers carry no WCS/rotation
  keywords, so true field rotation (alt-az smart telescopes rotate frame-to-frame)
  is not drawn. Centres and panel grouping are unaffected; only the drawn
  rectangle orientation is approximate.
- **Aladin Lite v2** (Canvas2D) is vendored locally in `web/aladin/` along with
  jQuery — so the app engine loads with no internet. We use v2 rather than v3
  because v3 requires WebGL2, which fails inside the GTK WebKit embed on
  XWayland+NVIDIA ("Failed to create GBM buffer"). The DSS **survey tiles** are
  still fetched over HTTP from CDS, so the sky background needs internet; the
  footprint/panel overlay works regardless.
- The frontend must not declare a global `$` — it would shadow jQuery's `$` and
  break Aladin. Use the `byId` helper in `web/app.js` instead.
- `commit` writes `panelizer_manifest.json` in the folder and refuses to run a
  second time until you **Undo** — so the operation is always reversible.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Layout

```
panelizer/        package (headers, geometry, cluster, organize, api, __main__)
web/              index.html / app.js / style.css  (pywebview frontend)
tests/            pytest suite
lights/           put your own FITS frames here (not included in the repo)
```

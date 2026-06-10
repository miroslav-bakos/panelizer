# Panelizer

[![Tests](https://github.com/miroslav-bakos/panelizer/actions/workflows/test.yml/badge.svg)](https://github.com/miroslav-bakos/panelizer/actions/workflows/test.yml)

A lightweight desktop tool for turning a folder of scattered FITS sub-exposures
(e.g. a Seestar / smart-telescope mosaic) into organised **panels**, grouped by
how close their pointing centres are on the sky.

![Panelizer screenshot](https://github.com/user-attachments/assets/bd91fcea-40ac-44aa-87bd-50101bd33d7a)

It can:

- read **RA/Dec**, focal length and pixel size straight from FITS headers,
- let you **override** focal length & pixel size (these drive plate scale / FOV),
- **group** frames into panels by a proximity threshold expressed as **% of the frame FOV**,
- do a **dry run** that just reports how many panels result — no files touched,
- **visualise** panels and their overlap over an Aladin Lite sky background, auto-fit to the field,
- on **commit**, move frames into `panel_NN/` subfolders (with one-click **Undo**).

## Install

**Python 3.10+ required.**

### Linux

The GUI needs **WebKit2GTK** system libraries (used by pywebview's GTK backend):

| Distro | Command |
|--------|---------|
| Arch / CachyOS | `sudo pacman -S python-gobject webkit2gtk-4.1` |
| Ubuntu / Debian | `sudo apt install python3-gi gir1.2-webkit2-4.1` |
| Fedora | `sudo dnf install python3-gobject webkit2gtk4.1` |

Then create a venv and install Python dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install pywebview astropy numpy
```

> **Arch / CachyOS tip:** if `astropy` and `numpy` are already installed
> system-wide, use `--system-site-packages` to inherit them and skip
> reinstalling: `python3 -m venv --system-site-packages .venv`

### macOS

Uses the built-in **WKWebView** — no extra system libraries needed:

```bash
python3 -m venv .venv
.venv/bin/pip install pywebview astropy numpy
```

### Windows

Requires **Microsoft Edge WebView2** (ships with Windows 11; free download for
Windows 10 from microsoft.com/en-us/edge/webview2):

```bat
python -m venv .venv
.venv\Scripts\pip install pywebview astropy numpy
```

## Run

**Linux / macOS:**

```bash
.venv/bin/python -m panelizer
```

**Windows:**

```bat
.venv\Scripts\python -m panelizer
```

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
# Linux / macOS
.venv/bin/python -m panelizer --cli lights/ --focal 250 --pixel 2.9 --threshold 15
.venv/bin/python -m panelizer --cli lights/ --threshold 15 --commit
.venv/bin/python -m panelizer --cli lights/ --threshold 15 --commit --copy --dest /path/to/out

# Windows
.venv\Scripts\python -m panelizer --cli lights\ --threshold 15 --commit
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
  because v3 requires WebGL2, which is unavailable in some embedded WebView
  configurations (notably GTK WebKit on XWayland+NVIDIA). The DSS **survey
  tiles** are still fetched over HTTP from CDS, so the sky background needs
  internet; the footprint/panel overlay works regardless.
- The frontend must not declare a global `$` — it would shadow jQuery's `$` and
  break Aladin. Use the `byId` helper in `web/app.js` instead.
- `commit` writes `panelizer_manifest.json` in the folder and refuses to run a
  second time until you **Undo** — so the operation is always reversible.

## Tests

```bash
.venv/bin/python  -m pytest tests/ -q   # Linux / macOS
.venv\Scripts\python -m pytest tests/ -q  # Windows
```

## Layout

```
panelizer/        package (headers, geometry, cluster, organize, api, __main__)
web/              index.html / app.js / style.css  (pywebview frontend)
tests/            pytest suite
lights/           put your own FITS frames here (not included in the repo)
```

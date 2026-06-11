/* Frontend logic: talks to the Python Api over pywebview's bridge, drives the
   Aladin Lite map, and keeps the control panel in sync. */

let aladin = null;
let panelOverlays = []; // per-panel overlays; cleared on each redraw
let scanned = false;
let lastResult = null;
let pendingDraw = null;   // result waiting for Aladin to finish init
let highlightOverlay = null;
let labelCatalog = null;
let selectedPanelId = null;
let mode = 'move';        // 'move' | 'copy'
let targetFolder = '';    // '' = same as source

// NB: named `byId`, not `$` — a top-level `const $` would shadow jQuery's
// global `$` (shared global lexical scope) and break Aladin v2.
const byId = (id) => document.getElementById(id);

// Distinct, evenly-spaced hues so adjacent panels read as different colours.
function panelColor(id, total) {
  const hue = (id * 137.508) % 360; // golden-angle spacing
  return `hsl(${hue.toFixed(0)}, 75%, 60%)`;
}

function setEnabled(id, on) { byId(id).disabled = !on; }

// --------------------------------------------------------------- Aladin v2 setup
// v2 uses Canvas2D — no WebGL required.
function initAladin() {
  try {
    aladin = A.aladin('#aladin', {
      survey: 'P/DSS2/color',
      fov: 2.0,
      target: '03 47 00 +24 07 00', // M45; replaced once data loads
      showReticle: false,
      showGrid: true,
      cooFrame: 'ICRS',
    });
    highlightOverlay = A.graphicOverlay({ color: '#ffd400', lineWidth: 3 });
    aladin.addOverlay(highlightOverlay);
    // Catalog used purely to render the panel-number labels at panel centres.
    labelCatalog = A.catalog({
      name: 'Panel #', shape: 'circle', sourceSize: 8, color: '#ffffff',
      displayLabel: true, labelColumn: 'id', labelColor: '#ffffff',
      labelFont: 'bold 14px sans-serif',
    });
    aladin.addCatalog(labelCatalog);
    const ph = byId('aladin-placeholder');
    if (ph) ph.style.display = 'none';
    if (pendingDraw) drawPanels(pendingDraw);
  } catch (err) {
    const ph = byId('aladin-placeholder');
    if (ph) ph.textContent = 'Sky map unavailable: ' + err;
    console.error('Aladin init error:', err);
  }
}

function drawPanels(result) {
  if (!aladin || !highlightOverlay) {
    pendingDraw = result;
    return;
  }
  pendingDraw = null;
  // Remove per-panel overlays from the previous draw by splicing them out of
  // Aladin v2's internal arrays (there is no removeOverlay() API in v2).
  if (panelOverlays.length) {
    const view = aladin.view;
    view.overlays = view.overlays.filter(o => !panelOverlays.includes(o));
    view.allOverlayLayers = view.allOverlayLayers.filter(o => !panelOverlays.includes(o));
    panelOverlays = [];
  }
  highlightOverlay.removeAll();
  labelCatalog.removeAll();
  selectedPanelId = null;
  const total = result.panels.length;
  const labelSources = [];
  for (const p of result.panels) {
    const color = panelColor(p.id, total);
    // One overlay per panel so Aladin v2's per-overlay colour applies correctly
    // (v2 draws all shapes in an overlay with a single strokeStyle; per-shape
    // colour options are silently ignored).
    const fpOverlay = A.graphicOverlay({ color, lineWidth: 1 });
    aladin.addOverlay(fpOverlay);
    panelOverlays.push(fpOverlay);
    // v2 polygon: array of [ra, dec] pairs
    for (const fp of p.footprints) {
      fpOverlay.add(A.polygon(fp));
    }
    // panel bounding box
    const [r0, r1, d0, d1] = p.bbox;
    const bbOverlay = A.graphicOverlay({ color, lineWidth: 2 });
    aladin.addOverlay(bbOverlay);
    panelOverlays.push(bbOverlay);
    bbOverlay.add(A.polygon([[r0, d0], [r1, d0], [r1, d1], [r0, d1]]));
    // number label at the panel centre
    labelSources.push(A.source(p.center_ra, p.center_dec, { id: String(p.id) }));
  }
  labelCatalog.addSources(labelSources);
  if (result.field) {
    aladin.gotoRaDec(result.field.center_ra, result.field.center_dec);
    aladin.setFoV(result.field.fov_deg);
  }
}

// Emphasise one panel: bright outline over its footprints + bbox, and recentre.
function selectPanel(id) {
  if (!lastResult || !highlightOverlay) return;
  const p = lastResult.panels.find((x) => x.id === id);
  if (!p) return;
  selectedPanelId = id;
  highlightOverlay.removeAll();
  for (const fp of p.footprints) {
    highlightOverlay.add(A.polygon(fp, { color: '#ffd400', lineWidth: 2 }));
  }
  const [r0, r1, d0, d1] = p.bbox;
  highlightOverlay.add(A.polygon([[r0, d0], [r1, d0], [r1, d1], [r0, d1]],
    { color: '#ffd400', lineWidth: 4 }));
  // mark the row and recentre (keep current zoom for context)
  document.querySelectorAll('#panel-table tbody tr').forEach((tr) => {
    tr.classList.toggle('selected', Number(tr.dataset.id) === id);
  });
  aladin.gotoRaDec(p.center_ra, p.center_dec);
}

// ----------------------------------------------------------------- table / UI
function fillTable(result) {
  const tbody = byId('panel-table').querySelector('tbody');
  tbody.innerHTML = '';
  const total = result.panels.length;
  for (const p of result.panels) {
    const tr = document.createElement('tr');
    tr.className = 'swatch';
    tr.dataset.id = p.id;
    tr.style.setProperty('--dot', panelColor(p.id, total));
    tr.innerHTML =
      `<td>${p.id}</td><td>${p.n_frames}</td>` +
      `<td>${p.center_ra.toFixed(3)}</td><td>${p.center_dec.toFixed(3)}</td>` +
      `<td>${p.total_exp.toFixed(0)}</td>`;
    tr.addEventListener('click', () => selectPanel(p.id));
    tbody.appendChild(tr);
  }
}

function updateScaleInfo() {
  const focal = parseFloat(byId('focal').value);
  const pix = parseFloat(byId('pix').value);
  if (focal > 0 && pix > 0) {
    const scale = 206.265 * pix / focal; // "/px
    byId('scale-info').textContent = `Plate scale ${scale.toFixed(3)}″/px`;
  }
}

function api() {
  return window.pywebview && window.pywebview.api;
}

// --------------------------------------------------------------- actions
async function doScan(folder) {
  const recursive = byId('recursive').checked;
  byId('scan-status').textContent = 'Scanning…';
  setEnabled('btn-dryrun', false);
  const info = await api().scan(folder, recursive);
  scanned = true;
  byId('focal').value = info.focal;
  byId('pix').value = info.pix;
  updateScaleInfo();
  let msg = `${info.n_frames} frames`;
  if (info.n_skipped) msg += ` · ${info.n_skipped} skipped (no RA/Dec)`;
  byId('scan-status').innerHTML = msg;
  setEnabled('btn-dryrun', info.n_frames > 0);
  setEnabled('btn-undo', info.has_manifest);
  byId('commit-status').textContent = info.has_manifest ?
    'Existing panel layout detected — Undo available.' : '';
  byId('panel-count').textContent = '—';
}

async function chooseFolder() {
  const r = await api().choose_folder();
  if (!r.folder) return;
  byId('folder-path').textContent = r.folder;
  await doScan(r.folder);
}

window.onScanProgress = function (done, total) {
  byId('scan-status').textContent = `Scanning… ${done}/${total}`;
};

async function dryRun() {
  if (!scanned) return;
  const focal = parseFloat(byId('focal').value);
  const pix = parseFloat(byId('pix').value);
  const pct = parseFloat(byId('threshold').value);
  byId('panel-count').textContent = '…';
  const result = await api().recompute(focal, pix, pct);
  lastResult = result;
  byId('panel-count').textContent = result.n_panels;
  fillTable(result);
  drawPanels(result);
  setEnabled('btn-commit', result.n_panels > 0);
  setEnabled('btn-export', result.n_panels > 0);
}

function updateCommitLabel() {
  const verb = mode === 'copy' ? 'Copy' : 'Move';
  byId('btn-commit').textContent = `${verb} files`;
}

// In-page confirmation — native window.confirm() blocks/fails in the WebKit embed.
function confirmModal(message, okLabel) {
  return new Promise((resolve) => {
    byId('modal-msg').textContent = message;
    byId('modal-ok').textContent = okLabel || 'Proceed';
    byId('modal').classList.remove('hidden');
    const ok = byId('modal-ok');
    const cancel = byId('modal-cancel');
    const done = (val) => {
      byId('modal').classList.add('hidden');
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      resolve(val);
    };
    const onOk = () => done(true);
    const onCancel = () => done(false);
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
  });
}

function fmtTime(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  return `${m}m ${String(s % 60).padStart(2, '0')}s`;
}

let commitStart = 0;
window.onCommitProgress = function (done, total) {
  const pct = total ? (done / total) * 100 : 0;
  byId('progress-fill').style.width = pct.toFixed(1) + '%';
  let eta = '';
  const elapsed = (Date.now() - commitStart) / 1000;
  if (done > 0 && done < total && elapsed > 0.2) {
    eta = ` · ~${fmtTime(((total - done) * elapsed) / done)} left`;
  }
  byId('progress-text').textContent = `${done} / ${total} files · ${pct.toFixed(0)}%${eta}`;
};

async function commit() {
  const verb = mode === 'copy' ? 'Copy' : 'Move';
  const where = targetFolder ? `\n\nTarget: ${targetFolder}` : '\n\n(into the source folder)';
  const ok = await confirmModal(
    `${verb} every frame into panel_NN/ subfolders?${where}`, `${verb} files`);
  if (!ok) return;

  commitStart = Date.now();
  byId('progress-fill').style.width = '0%';
  byId('progress-text').textContent = 'Starting…';
  byId('commit-progress').classList.remove('hidden');
  setEnabled('btn-cancel', true);
  setEnabled('btn-commit', false);
  setEnabled('btn-dryrun', false);
  byId('commit-status').textContent = mode === 'copy' ? 'Copying files…' : 'Moving files…';

  const r = await api().commit(mode, targetFolder || null);

  byId('commit-progress').classList.add('hidden');
  setEnabled('btn-dryrun', true);
  if (r.ok && r.cancelled) {
    byId('commit-status').innerHTML =
      `<span class="warn">Cancelled — rolled back ${r.n_rolled_back} files.</span>`;
    setEnabled('btn-commit', true);
  } else if (r.ok) {
    const verbed = r.mode === 'copy' ? 'Copied' : 'Moved';
    byId('commit-status').innerHTML =
      `<span class="ok">${verbed} ${r.n_files} files into ${r.n_panels} panels.</span>`;
    setEnabled('btn-commit', false);
    setEnabled('btn-undo', true);
  } else {
    setEnabled('btn-commit', true);
    byId('commit-status').innerHTML = `<span class="warn">${r.error}</span>`;
  }
}

async function cancelCommit() {
  setEnabled('btn-cancel', false);
  byId('progress-text').textContent = 'Cancelling — rolling back…';
  await api().cancel_commit();
}

function setMode(m) {
  mode = m;
  document.querySelectorAll('#mode-toggle .seg').forEach((b) => {
    b.classList.toggle('active', b.dataset.mode === m);
  });
  updateCommitLabel();
}

async function chooseTarget() {
  const r = await api().choose_target();
  if (!r.folder) return;
  targetFolder = r.folder;
  byId('target-path').value = r.folder;
}

function clearTarget() {
  targetFolder = '';
  byId('target-path').value = '';
}

async function undo() {
  byId('commit-status').textContent = 'Undoing…';
  const r = await api().undo();
  if (r.ok) {
    byId('commit-status').innerHTML = `<span class="ok">Restored ${r.n_restored} files.</span>`;
    setEnabled('btn-undo', false);
    setEnabled('btn-commit', lastResult && lastResult.n_panels > 0);
  } else {
    byId('commit-status').innerHTML = `<span class="warn">${r.error}</span>`;
  }
}

async function exportReport() {
  const r = await api().export_report();
  byId('commit-status').innerHTML = r.ok ?
    `<span class="ok">Wrote ${r.n_rows} rows to panels.json</span>` :
    `<span class="warn">${r.error}</span>`;
}

// ----------------------------------------------------------------- threshold
function onThreshold() {
  const pct = byId('threshold').value;
  byId('thresh-val').textContent = `${pct}% of FOV`;
  const focal = parseFloat(byId('focal').value);
  const pix = parseFloat(byId('pix').value);
  const n1 = 1080, n2 = 1920; // info-only estimate; real value comes from backend
  if (focal > 0 && pix > 0) {
    const fovMin = Math.min(n1, n2) * (206.265 * pix / focal) / 3600; // deg
    byId('link-info').textContent = `link radius ≈ ${(pct / 100 * fovMin * 60).toFixed(1)}′`;
  }
}

let debounce = null;
function onThresholdLive() {
  onThreshold();
  if (!scanned || !lastResult) return;
  clearTimeout(debounce);
  debounce = setTimeout(dryRun, 180); // live re-cluster as the slider moves
}

// ----------------------------------------------------------------- wire up
window.addEventListener('DOMContentLoaded', () => {
  initAladin();
  updateScaleInfo();
  onThreshold();
  byId('btn-folder').addEventListener('click', chooseFolder);
  byId('recursive').addEventListener('change', () => {
    const folder = byId('folder-path').textContent;
    if (scanned && folder && folder !== 'No folder selected') doScan(folder);
  });
  byId('btn-dryrun').addEventListener('click', dryRun);
  byId('btn-commit').addEventListener('click', commit);
  byId('btn-undo').addEventListener('click', undo);
  byId('btn-export').addEventListener('click', exportReport);
  byId('btn-target').addEventListener('click', chooseTarget);
  byId('btn-target-clear').addEventListener('click', clearTarget);
  byId('btn-cancel').addEventListener('click', cancelCommit);
  document.querySelectorAll('#mode-toggle .seg').forEach((b) => {
    b.addEventListener('click', () => setMode(b.dataset.mode));
  });
  updateCommitLabel();
  byId('threshold').addEventListener('input', onThresholdLive);
  byId('focal').addEventListener('input', () => { updateScaleInfo(); onThreshold(); });
  byId('pix').addEventListener('input', () => { updateScaleInfo(); onThreshold(); });
});

"""CSS, JavaScript and SVG icons for the HTML cards and the anywidget widgets."""
from __future__ import annotations

import json

# ------------------------------------------------------------------ theme + card CSS
# theme tokens (JupyterLab variable, VS Code variable, plain fallback), shared by the static cards and the widget
_TOKENS = [
    ("fg", "--jp-content-font-color1", "--vscode-foreground", "inherit"),
    ("muted", "--jp-content-font-color2", "--vscode-descriptionForeground", "rgba(128,128,128,.85)"),
    ("faint", "--jp-content-font-color3", "--vscode-disabledForeground", "rgba(128,128,128,.6)"),
    ("border", "--jp-border-color2", "--vscode-widget-border", "rgba(128,128,128,.25)"),
    ("border-strong", "--jp-border-color1", "--vscode-input-border", "rgba(128,128,128,.5)"),
    ("alt", "--jp-layout-color2", "--vscode-editorWidget-background", "rgba(128,128,128,.12)"),
    ("bg", "--jp-layout-color0", "--vscode-editor-background", "Canvas"),
    ("accent", "--jp-brand-color1", "--vscode-textLink-foreground", "#1976d2"),
    ("hl", "--jp-warn-color1", "--vscode-editorWarning-foreground", "#f57c00"),
    ("err", "--jp-error-color1", "--vscode-errorForeground", "#d32f2f"),
    ("code-bg", "--jp-cell-editor-background", "--vscode-textCodeBlock-background", "rgba(128,128,128,.08)"),
]
# VS Code's widget host defines light --jp-* colours whatever the theme, so in VS Code its own variables come first
_VSC = ":is(body[data-vscode-theme-kind],.vscode-light,.vscode-dark)"
_ROOT = f"{_VSC} :is(.sc,.stacsel){{" + "".join(f"--sv-{n}:var({vs},var({jp},{p}));" for n, jp, vs, p in _TOKENS) + "}\n"
_ROOT += ":is(.vscode-dark,[data-vscode-theme-kind*=dark]) :is(.sc,.stacsel){color-scheme:dark}\n"
_ROOT += ".sc,.stacsel{" + "".join(f"--sv-{n}:var({jp},var({vs},{p}));" for n, jp, vs, p in _TOKENS) + (
    "--sv-mono:var(--jp-code-font-family,var(--vscode-editor-font-family,ui-monospace,Menlo,Consolas,monospace));"
    "container-type:inline-size;position:relative;color:var(--sv-fg);"
    "font:var(--jp-ui-font-size1,13px)/1.45 var(--jp-ui-font-family,var(--vscode-font-family,system-ui,sans-serif))}\n"
    ".sc .lbl,.stacsel .lbl,.sc .ty{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--sv-muted)}\n"
    ".sc .lbl,.stacsel .lbl{margin:0 0 6px}\n"
)
_IN = ("font:inherit;color:var(--sv-fg);background:var(--sv-bg);border:1px solid var(--sv-border-strong);border-radius:3px;"
       "height:28px;padding:0 8px;box-sizing:border-box;color-scheme:inherit")
_IN_FOCUS = "outline:none;border-color:var(--sv-accent);box-shadow:0 0 0 1px var(--sv-accent)"

_CSS = "<style>" + _ROOT + """
.sc .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;padding-bottom:6px;border-bottom:1px solid var(--sv-border)}
.sc .tt{font-size:15px;font-weight:600}.sc .mt{margin-left:auto;font-size:12px;color:var(--sv-muted)}
.sc .m{color:var(--sv-muted)}.sc .nw{white-space:nowrap}
.sc .ds{white-space:pre-line;margin:8px 0 0;max-width:110ch;color:var(--sv-muted)}.sc .ds:empty{display:none}
.sc details.dsc{border:0;margin:8px 0 0;overflow:visible}.sc .dsc>summary{font-weight:400;list-style:none}
.sc .dsc>summary::-webkit-details-marker{display:none}.sc .dsc[open]>summary .dmore{display:none}
.sc .dsc>summary .dless{display:none;font-size:11px;color:var(--sv-faint)}.sc .dsc[open]>summary .dless{display:inline}
.sc .grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:20px;margin-top:12px}
.sc .grid.one{grid-template-columns:minmax(0,1fr)}
.sc .grid.eq{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-auto-rows:420px}
@container (max-width:720px){.sc .grid,.sc .grid.eq{grid-template-columns:minmax(0,1fr)}}
.sc .mp,.sc .info{min-width:0}
.sc .map{aspect-ratio:2/1;min-height:240px;max-height:440px;border:1px solid var(--sv-border);border-radius:3px;position:relative;overflow:hidden}
.sc .grid.eq .map{aspect-ratio:auto;height:100%;max-height:none}.sc .map svg{display:block}
.sc .kv{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 16px;margin:0}
.sc .kv dt{color:var(--sv-muted)}.sc .kv dd{margin:0;overflow-wrap:anywhere}
.sc .bar{position:relative;height:6px;background:var(--sv-alt);border-radius:3px;margin:4px 0 3px}
.sc .bar>div{position:absolute;top:0;bottom:0;background:var(--sv-accent);border-radius:3px}
.sc .tk{display:flex;justify-content:space-between;font-size:11px;color:var(--sv-faint)}.sc .dt{margin:4px 0 16px}
.sc details{border-top:1px solid var(--sv-border);margin-top:14px;overflow-x:auto}.sc details+details{margin-top:0}
.sc details:not(.dsc)>summary{cursor:pointer;list-style:none;display:flex;gap:8px;align-items:center;padding:7px 0;font-weight:600}
.sc details:not(.dsc)>summary::-webkit-details-marker{display:none}
.sc details:not(.dsc)>summary::before{content:"\\25B8";color:var(--sv-muted);font-size:11px;transition:transform .15s}
.sc details:not(.dsc)[open]>summary::before{transform:rotate(90deg)}
.sc details:not(.dsc)>summary span{font-weight:400;color:var(--sv-muted)}.sc details>.kv{padding-bottom:10px}
.sc .il{overflow:auto;border:1px solid var(--sv-border);border-radius:3px}
.sc .tb{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.sc .tb th{position:sticky;top:0;z-index:1;background:var(--sv-bg);text-align:left;font-weight:400;font-size:11px;letter-spacing:.06em;
 text-transform:uppercase;color:var(--sv-muted);padding:5px 10px;border-bottom:1px solid var(--sv-border-strong)}
.sc .tb td{padding:4px 10px;border-bottom:1px solid var(--sv-border);white-space:nowrap;text-align:left}
.sc .tb td.id{max-width:0;width:100%;overflow:hidden;text-overflow:ellipsis;font-family:var(--sv-mono);font-size:12px}
.sc .ovl{max-height:480px}
.sc .at{margin-bottom:10px}.sc .at td.k{font-family:var(--sv-mono);font-size:12px}
.sc .at td:last-child{white-space:normal;overflow-wrap:anywhere;width:100%}
.sc .ov td.id{max-width:none;width:1%}.sc .ov td.m{white-space:normal}
.sc .tb tbody tr:nth-child(even){background:color-mix(in srgb,var(--sv-alt) 45%,transparent)}
.sc .tb tr[data-i]{cursor:pointer}
.sc .tb tr.hov,.sc .tb tr[data-q]:hover{background:color-mix(in srgb,var(--sv-accent) 14%,transparent)}
.sc .tb tr.sel{background:color-mix(in srgb,var(--sv-hl) 18%,transparent);box-shadow:inset 3px 0 var(--sv-hl)}
.sc .cb{display:inline-block;width:38px;height:4px;border-radius:2px;background:var(--sv-alt);vertical-align:middle;margin-right:6px;position:relative;overflow:hidden}
.sc .cb>i{position:absolute;inset:0 auto 0 0;background:var(--sv-muted)}
.sc .pop{display:flex;gap:10px;align-items:flex-start;font-size:12px}
.sc .pop img{display:block;flex:none;width:160px;max-height:160px;object-fit:contain}
.sc .pop>div{min-width:0;max-width:220px}.sc .pop div div{color:var(--sv-muted)}.sc .pop div .pt{color:inherit;margin-bottom:2px}
.sc .pop b{display:block;margin-bottom:3px;font:11px var(--sv-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc .tip{position:absolute;z-index:1000;max-width:360px;padding:8px 10px;pointer-events:none;background:var(--sv-bg);color:var(--sv-fg);
 border:1px solid var(--sv-border-strong);border-left:3px solid var(--sv-accent);border-radius:4px;box-shadow:0 4px 14px rgba(0,0,0,.18);font-size:12px;line-height:1.45}
.sc .tip[hidden]{display:none}.sc .tip b{display:block;margin-bottom:3px;font-size:13px}.sc .tip div{color:var(--sv-muted)}
.sc .tip img{display:block;width:100%;max-height:180px;object-fit:contain;margin-bottom:6px;border-radius:3px}
.sc .tip img[hidden]{display:none}
.sc .q{position:relative;display:flex;align-items:center;margin:12px 0 8px;color:var(--sv-muted)}.sc .q[hidden]{display:none}
.sc .q svg{position:absolute;left:10px;width:14px;height:14px;pointer-events:none}
.sc .q input{""" + _IN + """;width:100%;height:32px;padding:0 90px 0 32px;border-radius:4px}.sc .q input:focus{""" + _IN_FOCUS + """}
.sc .q input::placeholder{color:var(--sv-faint)}.sc .q input:hover:not(:focus){border-color:var(--sv-muted)}
.sc .q span{position:absolute;right:32px;font-size:11px;color:var(--sv-muted);pointer-events:none}
.sc .ov td.ti{width:100%;white-space:normal}.sc .ov td.id{width:1%;color:var(--sv-muted)}
.sc .cp{display:inline-flex;vertical-align:middle;margin-left:6px;padding:2px;border:0;border-radius:3px;background:none;color:var(--sv-muted);cursor:pointer;opacity:0}
.sc .cp svg{width:13px;height:13px}.sc .cp[hidden]{display:none}.sc .cp:hover{color:var(--sv-accent);background:var(--sv-alt)}
.sc tr:hover .cp,.sc .cp:focus-visible,.sc .cp.ok{opacity:1}.sc .cp.ok{color:var(--sv-accent)}
.sc.pick tr[data-k]{cursor:pointer}.sc.pick tr[data-k]:hover td{background:var(--sv-alt)}
.sc .ov tr.sel td{background:color-mix(in srgb,var(--sv-accent) 16%,transparent)}.sc .ov tr.sel td.ti{font-weight:600}
</style>"""


# ------------------------------------------------------------------ Leaflet maps
_TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/NAME/MapServer/tile/{z}/{y}/{x}"  # NAME: World_{Light,Dark}_Gray_{Base,Reference}
_TILE_ATTR = "&copy; Esri, OSM"
# Leaflet chrome in theme tokens; injected once per page (the tokens live on .sc / .stacsel)
_LEAF_CSS = "".join(":is(.sc,.stacsel) " + r + "\n" for r in [
    ".leaflet-container{background:var(--sv-alt);font:inherit}",
    ".leaflet-container .leaflet-control-attribution{font-size:9px;line-height:1.2;padding:0 4px;color:var(--sv-muted);"
    "background:color-mix(in srgb,var(--sv-bg) 70%,transparent)}",
    ".leaflet-container .leaflet-control-attribution a{color:inherit}",
    ".leaflet-bar a{background:var(--sv-bg);color:var(--sv-fg);border-color:var(--sv-border)}",
    ".leaflet-bar a:hover{background:var(--sv-alt)}.leaflet-bar{border:1px solid var(--sv-border-strong);box-shadow:none}",
    ".leaflet-popup-content-wrapper,.leaflet-popup-tip{background:var(--sv-bg);color:var(--sv-fg);box-shadow:0 3px 12px rgba(0,0,0,.25)}",
    ".leaflet-popup-content-wrapper{border-radius:4px}.leaflet-popup-content{margin:10px 12px;font-size:12px;line-height:1.45}",
    ".leaflet-container a.leaflet-popup-close-button{color:var(--sv-muted)}.leaflet-popup-content a{color:var(--sv-accent)}",
    ".leaflet-tooltip{background:var(--sv-bg);color:var(--sv-fg);border-color:var(--sv-border-strong);box-shadow:none}",
])
_DARK_JS = """
const isDark = el => {  // light text => dark theme (JupyterLab, VS Code and Colab all set the text colour)
  const [r, g, b] = (getComputedStyle(el).color.match(/[\\d.]+/g) || []).map(Number);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 128;
};
"""
_LEAFLET_JS = (f"const TILE = {json.dumps(_TILE)}, TILE_ATTR = {json.dumps(_TILE_ATTR)}, TILE_MAX = 16, LEAF_CSS = {json.dumps(_LEAF_CSS)};\n"
               + _DARK_JS + """
const LEAF = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/";
const tok = (el, n, d) => getComputedStyle(el).getPropertyValue(n).trim() || d;  // theme token as a concrete colour
async function leaflet(el) {  // CSS once per page (or shadow root, e.g. marimo); dynamic import avoids RequireJS clashes
  const root = el.getRootNode(), shadow = root instanceof ShadowRoot, at = shadow ? root : document.head;
  const has = id => (shadow ? root : document).getElementById(id);
  if (!has("stacmap-css")) {
    const l = document.createElement("link");
    l.id = "stacmap-css"; l.rel = "stylesheet"; l.href = LEAF + "leaflet.css";
    await new Promise(r => { l.onload = l.onerror = r; at.append(l); });
  }
  if (!has("stacmap-style")) {
    const st = document.createElement("style");
    st.id = "stacmap-style"; st.textContent = LEAF_CSS;
    at.append(st);
  }
  return import(LEAF + "leaflet-src.esm.js");
}
function basemap(L, el) {
  const map = L.map(el, { scrollWheelZoom: false, worldCopyJump: true, maxZoom: TILE_MAX, zoomSnap: 0 });
  const url = k => TILE.replace("NAME", "World_" + (isDark(el) ? "Dark" : "Light") + "_Gray_" + k);
  map.attributionControl.setPrefix(false);
  L.tileLayer(url("Base"), { maxZoom: TILE_MAX, attribution: TILE_ATTR }).addTo(map);
  L.tileLayer(url("Reference"), { maxZoom: TILE_MAX }).addTo(map);  // labels on top of the base
  return map;
}
function fitView(map, el, b, pad) {  // global extents fill the map width instead of letterboxing
  const world = b.getEast() - b.getWest() >= 350 && b.getNorth() - b.getSouth() >= 170;
  world ? map.setView([15, 0], Math.log2(el.clientWidth / 256), { animate: false }) : map.fitBounds(b, { padding: pad });
}
""")

# body of the async IIFE in the static cards; `id` and `data` ({bboxes?, features?}) are defined around it
_MAP_JS = """
const el = document.getElementById(id), fallback = el.innerHTML;
let map, ro;
try {
  const L = await leaflet(el);
  el.innerHTML = "";
  map = basemap(L, el);
  const tip = s => { const d = document.createElement("div"); d.textContent = s; return d; };  // no innerHTML
  const group = L.featureGroup().addTo(map);
  const ACC = tok(el, "--sv-accent", "#1976d2"), HL = tok(el, "--sv-hl", "#f57c00");
  const style = { color: ACC, weight: 1, fillColor: ACC };
  const bboxes = data.bboxes || [];
  bboxes.forEach((b, i) => {
    const spans = b[0] <= b[2] ? [[b[0], b[2]]] : [[b[0], 180], [-180, b[2]]];  // antimeridian
    for (const [a, c] of spans)
      L.rectangle([[b[1], a], [b[3], c]], { ...style, fillOpacity: i ? 0.2 : 0.35 })
        .bindTooltip(tip("[" + b.map(v => v.toFixed(2)).join(", ") + "]")).addTo(group);
  });
  if (data.features) {
    const base = { ...style, opacity: 1, fillOpacity: 0.05 }, hl = { color: HL, fillColor: HL, weight: 2, opacity: 1, fillOpacity: 0.45 };
    const layers = [], rows = {}, http = u => /^https?:\\/\\//i.test(u || "");
    (el.closest(".sc") || document).querySelectorAll("tr[data-i]").forEach(r => rows[r.dataset.i] = r);  // list rows, if any
    const row = (i, c, on) => rows[i] && rows[i].classList.toggle(c, on);
    const dim = { opacity: 0.25, fillOpacity: 0.06 }, pt = { ...base, fillOpacity: 0.6 };  // points need a visible fill
    const rest = f => /Point$/.test(f.geometry.type) ? pt : base;
    let sel, hv;  // selected (clicked) and hovered item
    const paint = () => layers.forEach((l, j) => l.setStyle(j === sel || j === hv ? hl : hv != null ? dim : rest(l.feature)));
    const hover = (i, on) => {
      if (hv != null) row(hv, "hov", false);
      hv = on ? i : null;
      if (on) { layers[i].bringToFront(); row(i, "hov", true); }
      paint();
    };
    const open = (i, scroll) => {
      const p = layers[i].feature.properties, box = document.createElement("div"), d = document.createElement("div"), add = (t, s) => {
        const e = document.createElement(t); e.textContent = s; d.appendChild(e); return e; };
      const pop = L.popup({ maxWidth: 400, autoPanPaddingTopLeft: [50, 10] });  // clear of the zoom control
      box.className = "pop";
      if (http(p.thumb)) {  // image beside the text; re-pan once its size is known
        const m = document.createElement("img"); m.onload = () => pop.update(); m.src = p.thumb; m.alt = p.id; box.appendChild(m); }
      box.appendChild(d); add("b", p.id).title = p.id;
      if (p.title) add("div", p.title).className = "pt";
      add("div", p.time);
      for (const [k, v] of Object.entries(p.props || {})) add("div", k + ": " + v);
      if (http(p.href)) { const a = add("a", "item JSON"); a.href = p.href; a.target = "_blank"; a.rel = "noopener"; }
      const l = layers[i];  // markers have a position, shapes have bounds
      pop.setLatLng(l.getLatLng ? l.getLatLng() : l.getBounds().getCenter()).setContent(box).openOn(map);  // closes the previous popup first
      sel = i; row(i, "sel", true); paint();
      const r = rows[i], c = r && r.closest(".il");
      if (scroll && c) c.scrollTop += r.getBoundingClientRect().top - c.getBoundingClientRect().top - (c.clientHeight - r.offsetHeight) / 2;  // list only, not the page
    };
    map.on("popupclose", () => { if (sel != null) row(sel, "sel", false); sel = null; paint(); });
    L.geoJSON(data.features, { style: rest, pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5 }), onEachFeature: (f, l) => {
      const i = f.properties.i; layers[i] = l;
      l.bindTooltip(tip(f.properties.t)).on({ mouseover: () => hover(i, true), mouseout: () => hover(i, false), click: () => open(i, true) });
      if (rows[i]) rows[i].onmouseenter = () => hover(i, true), rows[i].onmouseleave = () => hover(i, false), rows[i].onclick = () => open(i);
    } }).addTo(group);
  }
  let fitted = false;
  const fit = () => { fitView(map, el, group.getBounds(), [12, 12]); fitted = true; };
  if (el.clientWidth > 0) fit();  // zero-size (hidden) containers would zoom to max, so wait for a size
  ro = new ResizeObserver(() => { map.invalidateSize(); if (!fitted && el.clientWidth > 0) fit(); });
  ro.observe(el);
} catch (err) {  // keep the SVG fallback
  if (ro) ro.disconnect();
  if (map) map.remove();
  el.innerHTML = fallback;
}
"""


# ------------------------------------------------------------------------- icons
_ICON = "<svg viewBox='0 0 16 16' fill='none' stroke='currentColor' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{}</svg>"
_SEARCH_SVG = _ICON.format("<circle cx='7' cy='7' r='4.5'/><path d='M10.5 10.5 14 14'/>")
_COPY_SVG = _ICON.format("<rect x='5.5' y='5.5' width='8' height='8' rx='1.5'/><path d='M10.5 5.5V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2'/>")
_CHECK_SVG = _ICON.format("<path d='M3 8.5 6.5 12 13 4.5'/>")


# ------------------------------------------------------------ interactive widgets
_COPY_JS = f"const COPY = {json.dumps(_COPY_SVG)}, CHECK = {json.dumps(_CHECK_SVG)};" + """
function copyBtn(el, cp, text) {  // copy button for a code block; text() gives the current code
  cp.innerHTML = COPY;
  cp.addEventListener("click", async () => {
    const s = text();
    try { await navigator.clipboard.writeText(s); } catch {  // clipboard API can be blocked (e.g. VS Code output iframes)
      const t = document.createElement("textarea"); t.value = s; el.append(t); t.select(); document.execCommand("copy"); t.remove(); }
    cp.innerHTML = CHECK; cp.classList.add("ok");
    setTimeout(() => { cp.innerHTML = COPY; cp.classList.remove("ok"); }, 1200);
  });
}
"""
_ESM = _LEAFLET_JS + _COPY_JS + """
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const DAY = 864e5, ms = s => Date.parse(s), ymd = t => new Date(t).toISOString().slice(0, 10);  // UTC day strings
const rb = b => [[b[1], b[0]], [b[3], b[2]]];  // [w, s, e, n] -> Leaflet bounds
const HINT = {
  "": "Draw a bbox or a polygon on the map",
  bbox: "Click, then drag on the map to set the bbox (Esc cancels)",
  poly: "Click to add vertices; double-click or click the first vertex to finish (Esc cancels)",
};

async function render({ model, el }) {
  el.classList.add("stacsel");
  el.style.colorScheme = isDark(el) ? "dark" : "light";  // native date picker / select popups follow the theme
  el.innerHTML = `
    <div class="sg">
      <div class="stacsel-map">
        <div class="stacsel-tools">
          <span class="stacsel-seg">
            <button type="button" class="bb">\u25A2 Bbox</button>
            <button type="button" class="pg">\u2B20 Polygon</button>
            <button type="button" class="rs">\u21BA Reset</button>
          </span>
          <span class="stacsel-hint"></span>
        </div>
      </div>
      <div class="stacsel-panel">
        <div>
          <p class="lbl">Date range</p>
          <div class="stacsel-row">
            <label>Start<input type="date" class="d0"></label>
            <label>End<input type="date" class="d1"></label>
            <span class="stacsel-span"></span>
          </div>
          <div class="stacsel-sl"><div class="stacsel-fill"></div><input type="range" class="r0" min="0" step="1"><input type="range" class="r1" min="0" step="1"></div>
          <div class="stacsel-tk"><span class="t0"></span><span class="t1"></span></div>
          <div class="stacsel-pre"><span class="stacsel-seg">
            <button type="button" data-n="7">7 d</button>
            <button type="button" data-n="30">30 d</button>
            <button type="button" data-n="365">1 y</button>
            <button type="button" data-n="0">All</button>
          </span></div>
          <div class="stacsel-err"></div>
        </div>
        <div class="stacsel-opts">
          <p class="lbl">Filters</p>
          <div class="stacsel-row">
            <label class="cc"><span>Max cloud cover &middot; <span class="ccv"></span></span><input type="range" class="cc" min="0" max="100" step="5"></label>
            <span class="stacsel-sels"></span>
          </div>
        </div>
        <div><p class="lbl">Search call</p><div class="stacsel-cw"><pre class="stacsel-code"></pre>
          <button type="button" class="stacsel-cp" aria-label="Copy search call"></button></div></div>
      </div>
    </div>`;
  const q = s => el.querySelector(s);
  const err = q(".stacsel-err"), bb = q(".bb"), pg = q(".pg"), hint = q(".stacsel-hint");
  const d0 = q(".d0"), d1 = q(".d1"), r0 = q(".r0"), r1 = q(".r1"), cc = q("input.cc"), sels = q(".stacsel-sels");
  const fill = q(".stacsel-fill"), span = q(".stacsel-span");
  const axis = () => {  // slider axis: min_date (or 1980) .. max_date (or today), in days
    const mn = model.get("min_date") || "1980-01-01", mx = model.get("max_date") || ymd(Date.now());
    return [mn, mx, Math.max((ms(mx) - ms(mn)) / DAY, 1)];
  };
  const code = q(".stacsel-code"), mapEl = q(".stacsel-map"), tools = q(".stacsel-tools");
  for (const t of ["mousedown", "click", "dblclick", "touchstart"]) tools.addEventListener(t, e => e.stopPropagation());  // not map input
  const pt = p => "[" + p.map(v => v.toFixed(2)).join(", ") + "]";
  let ext, sel, pl, map, mode = "";  // rectangles + polygon, created once the map is up
  function draw() {
    const b = model.get("bbox"), poly = model.get("polygon"), cloud = model.get("cloud_cover"), flt = model.get("filters");
    if (sel) {
      ext.setBounds(rb(model.get("extent"))); sel.setBounds(rb(b));
      const whole = b.every((v, i) => Math.abs(v - model.get("extent")[i]) < 1e-6);  // nothing drawn yet: outline only
      sel.setStyle({ opacity: whole ? 0 : 1, fillOpacity: whole ? 0 : 0.18 });
      pl.setLatLngs(poly.map(p => [p[1], p[0]]));
      (poly.length ? sel : pl).remove(); (poly.length ? pl : sel).addTo(map);
    }
    d0.value = model.get("start"); d1.value = model.get("end");
    const [mn, mx, n] = axis(), lo = model.get("min_date"), hi = model.get("max_date");
    d0.min = mn; d0.max = d1.value || mx; d1.min = d0.value || mn; d1.max = mx;
    const bad = d0.value && d1.value && d0.value > d1.value;
    const out = v => v && ((lo && v < lo) || (hi && v > hi));
    d0.classList.toggle("bad", !!(bad || out(d0.value))); d1.classList.toggle("bad", !!(bad || out(d1.value)));
    err.textContent = bad ? "Start must be before end" : out(d0.value) || out(d1.value) ? "Date outside the collection's range" : "";
    const day = (v, dflt) => v ? clamp((ms(v) - ms(mn)) / DAY, 0, n) : dflt;
    r0.max = r1.max = n; r0.value = day(d0.value, 0); r1.value = day(d1.value, n);
    r0.style.zIndex = +r0.value > n / 2 ? 3 : 1;  // keep the handle with room to move on top
    const a = r0.value / n, c = Math.max(r1.value / n, a);
    fill.style.left = `calc(7px + (100% - 14px) * ${a})`; fill.style.width = `calc((100% - 14px) * ${c - a})`;
    q(".t0").textContent = mn; q(".t1").textContent = mx;
    const days = d0.value && d1.value && !bad ? (ms(d1.value) - ms(d0.value)) / DAY + 1 : 0;
    span.textContent = days ? `${days} day${days > 1 ? "s" : ""}` : "";
    const hasCloud = model.get("has_cloud");
    q("label.cc").style.display = hasCloud ? "" : "none";
    q(".stacsel-opts").style.display = hasCloud || sels.children.length ? "" : "none";
    cc.value = cloud; cc.style.setProperty("--p", cloud + "%"); q(".ccv").textContent = cloud >= 100 ? "any" : `\\u2264 ${cloud}%`;
    sels.querySelectorAll("select").forEach(s => { s.value = flt[s.dataset.k] || ""; });
    const query = {};
    if (cloud < 100) query["eo:cloud_cover"] = { lte: cloud };
    for (const [k, v] of Object.entries(flt)) query[k] = { eq: v };
    const where = poly.length
      ? `intersects={"type": "Polygon", "coordinates": [[${poly.concat([poly[0]]).map(pt).join(", ")}]]}`
      : `bbox=${pt(b)}`;
    code.textContent = `search(\\n  ${where},\\n  datetime="${d0.value}/${d1.value}",${Object.keys(query).length ? `\\n  query=${JSON.stringify(query).replace(/":/g, '": ').replace(/,"/g, ', "')},` : ""}\\n)`;
  }
  const setDate = (k, v) => { model.set(k, v); model.save_changes(); draw(); };
  d0.addEventListener("change", () => setDate("start", d0.value));
  d1.addEventListener("change", () => setDate("end", d1.value));
  const setRange = (a, b) => { model.set("start", a); model.set("end", b); model.save_changes(); draw(); };
  const day = v => ymd(ms(axis()[0]) + v * DAY);
  r0.addEventListener("input", () => setDate("start", day(Math.min(+r0.value, +r1.value))));
  r1.addEventListener("input", () => setDate("end", day(Math.max(+r0.value, +r1.value))));
  el.querySelectorAll(".stacsel-pre button").forEach(b => b.addEventListener("click", () => {
    const [mn, mx] = axis(), n = +b.dataset.n;
    n ? setRange(ymd(Math.max(ms(mx) - n * DAY, ms(mn))), mx) : setRange(model.get("min_date"), mx);
  }));
  cc.addEventListener("input", () => setDate("cloud_cover", +cc.value));
  const cp = q(".stacsel-cp");
  copyBtn(el, cp, () => code.textContent);
  function buildSels() {  // one <select> per filter option
    sels.innerHTML = "";
    for (const [k, vals] of Object.entries(model.get("filter_options"))) {
      const lab = document.createElement("label"), s = document.createElement("select");
      lab.append(k.split(":").pop().replace(/_/g, " "), s); s.dataset.k = k;
      for (const v of ["", ...vals]) s.add(new Option(v || "any", v));
      s.addEventListener("change", () => {
        const f = { ...model.get("filters") };
        s.value ? f[k] = s.value : delete f[k];
        setDate("filters", f);
      });
      sels.append(lab);
    }
    draw();
  }
  for (const k of ["bbox", "polygon", "start", "end", "max_date", "min_date", "cloud_cover", "filters", "has_cloud"]) model.on("change:" + k, draw);
  model.on("change:filter_options", buildSels);
  buildSels();

  const L = await leaflet(el);
  map = basemap(L, mapEl);
  const ACC = tok(mapEl, "--sv-accent", "#1976d2");
  const none = { interactive: false }, style = { ...none, color: ACC, weight: 1, fillColor: ACC, fillOpacity: 0.3 };
  ext = L.rectangle(rb(model.get("extent")), { ...none, color: tok(mapEl, "--sv-muted", "#888"), weight: 1, dashArray: "4 3", fill: false }).addTo(map);
  sel = L.rectangle(rb(model.get("bbox")), style).addTo(map);
  pl = L.polygon([], style);
  const tmp = L.layerGroup().addTo(map), rubber = L.polyline([], { ...none, color: ACC, weight: 1.5, dashArray: "4 3" });
  draw();
  let fitted = false;
  const fit = () => {  // open on the current selection when one was drawn, else the whole extent
    const drawn = model.get("polygon").length ? pl : sel.options.opacity ? sel : ext;
    fitView(map, mapEl, drawn.getBounds(), drawn === ext ? [10, 10] : [40, 40]); fitted = true;
  };
  if (mapEl.clientWidth > 0) fit();  // zero-size (hidden) containers would zoom to max, so wait for a size
  const ro = new ResizeObserver(() => { map.invalidateSize(); if (!fitted && mapEl.clientWidth > 0) fit(); });
  ro.observe(mapEl);

  let anchor = null, verts = [];
  function setMode(m) {
    mode = m; anchor = null; verts = []; tmp.clearLayers(); rubber.setLatLngs([]); rubber.addTo(tmp);
    bb.classList.toggle("on", m === "bbox"); pg.classList.toggle("on", m === "poly");
    mapEl.style.cursor = m ? "crosshair" : ""; hint.textContent = HINT[m];
    for (const h of [map.dragging, map.doubleClickZoom]) h[m ? "disable" : "enable"]();
  }
  setMode("");
  bb.addEventListener("click", () => setMode(mode === "bbox" ? "" : "bbox"));
  pg.addEventListener("click", () => setMode(mode === "poly" ? "" : "poly"));
  q(".rs").addEventListener("click", () => {
    setMode(""); model.set("polygon", []); model.set("bbox", [...model.get("extent")]); model.save_changes(); draw();
  });
  const px = p => map.latLngToContainerPoint(p);
  function done() {
    if (verts.length < 3) return;
    const ring = verts.map(v => [clamp(v.lng, -180, 180), clamp(v.lat, -90, 90)]);
    const xs = ring.map(p => p[0]), ys = ring.map(p => p[1]);
    model.set("polygon", ring); model.set("bbox", [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]);
    model.save_changes(); setMode(""); draw();
  }
  map.on("mousedown", e => {
    if (mode !== "bbox") return;
    anchor = e.latlng;
    if (model.get("polygon").length) { model.set("polygon", []); draw(); }
  });
  map.on("mousemove", e => {
    if (mode === "poly" && verts.length) rubber.setLatLngs([...verts, e.latlng]);
    if (!anchor) return;
    const a = anchor, p = e.latlng;
    model.set("bbox", [clamp(Math.min(a.lng, p.lng), -180, 180), clamp(Math.min(a.lat, p.lat), -90, 90),
                       clamp(Math.max(a.lng, p.lng), -180, 180), clamp(Math.max(a.lat, p.lat), -90, 90)]);
    draw();
  });
  map.on("click", e => {
    if (mode !== "poly") return;
    if (verts.length >= 3 && px(e.latlng).distanceTo(px(verts[0])) <= 8) return done();
    if (verts.length && px(e.latlng).distanceTo(px(verts[verts.length - 1])) <= 2) return;  // 2nd click of a double-click
    verts.push(e.latlng); rubber.setLatLngs([...verts, e.latlng]);
    if (verts.length === 1) L.circleMarker(e.latlng, { ...none, radius: 5, color: ACC, fillOpacity: 1 }).addTo(tmp);
  });
  map.on("dblclick", () => { if (mode === "poly") done(); });
  const finish = () => { if (!anchor) return; model.save_changes(); setMode(""); };
  const esc = e => { if (e.key === "Escape" && mode) setMode(""); };
  window.addEventListener("mouseup", finish);  // also fires when released outside the map
  window.addEventListener("keydown", esc);
  return () => { window.removeEventListener("mouseup", finish); window.removeEventListener("keydown", esc); ro.disconnect(); };
}
export default { render };
"""

_OPEN_ESM = _COPY_JS + _DARK_JS + f"const SEARCH = {json.dumps(_SEARCH_SVG)};" + """
function render({ model, el }) {
  el.classList.add("stacsel");
  el.style.colorScheme = isDark(el) ? "dark" : "light";
  el.innerHTML = `
    <div class="stacsel-panel">
      <div><p class="lbl">Assets</p>
        <div class="stacsel-atb"><span class="stacsel-af">${SEARCH}<input type="search" placeholder="Filter by key, title or band"></span>
          <span class="stacsel-seg stacsel-dt"><button type="button" data-d="1">Data</button><button type="button" data-d="">All</button></span>
          <span class="stacsel-seg"><button type="button" class="clr">Clear all</button></span></div>
        <div class="stacsel-assets"></div></div>
      <div class="stacsel-pars"><p class="lbl">Settings</p><div class="stacsel-row"></div></div>
      <div><details class="stacsel-oc"><summary class="lbl">Open call</summary><div class="stacsel-cw"><pre class="stacsel-code"></pre>
        <button type="button" class="stacsel-cp" aria-label="Copy open call"></button></div></details>
        <div class="stacsel-err"></div></div>
    </div>`;
  const q = s => el.querySelector(s);
  const list = q(".stacsel-assets"), row = q(".stacsel-pars .stacsel-row"), code = q(".stacsel-code"), err = q(".stacsel-err");
  const setAssets = a => { model.set("assets", a); model.save_changes(); };
  const setValue = (k, v) => { model.set("values", { ...model.get("values"), [k]: v }); model.save_changes(); };
  const fin = q(".stacsel-af input");
  let dataOnly = model.get("asset_options").some(o => o.data);
  const pick = (name, keys, on) => {  // add/remove keys; another opener's keys replace the selection
    const cur = model.get("assets"), next = !on ? cur.filter(k => !keys.includes(k)) : name === model.get("opener") ? [...cur, ...keys] : keys;
    setAssets(model.get("asset_options").map(a => a.key).filter(k => next.includes(k)));
  };
  function buildAssets() {  // one group per opener; assets without one are disabled
    list.innerHTML = "";
    const groups = {}, opts = model.get("asset_options");
    for (const o of opts) (groups[o.opener || ""] ||= []).push(o);
    for (const [name, os] of Object.entries(groups)) {
      const g = document.createElement("div"), h = document.createElement(name ? "button" : "div");
      g.className = "stacsel-grp"; h.className = "stacsel-gh"; h.textContent = name || "no opener"; g.append(h);
      if (name) {  // heading toggles every shown asset of the group
        h.type = "button"; h.title = "Select / clear all shown " + name + " assets";
        h.addEventListener("click", () => {
          const keys = [...g.querySelectorAll("label:not([hidden]) input")].map(cb => cb.value), sel = model.get("assets");
          pick(name, keys, !keys.every(k => sel.includes(k)));
        });
      }
      for (const o of os) {
        const lab = document.createElement("label"), cb = document.createElement("input");
        cb.type = "checkbox"; cb.value = o.key; cb.disabled = !name;
        cb.addEventListener("change", () => pick(name, [o.key], cb.checked));
        lab.dataset.s = [o.key, o.title, ...o.bands].join(" ").toLowerCase(); lab.dataset.d = o.data ? "1" : "";
        lab.title = o.title || o.key; lab.append(cb, o.label);
        if (o.bands.length) { const b = document.createElement("span"); b.className = "stacsel-bn"; b.textContent = o.bands.join(", "); lab.append(b); }
        g.append(lab);
      }
      list.append(g);
    }
    q(".stacsel-dt").hidden = !opts.some(o => o.data) || opts.every(o => o.data);
    q(".stacsel-af").hidden = opts.length < 8;
    sync();
  }
  function filter() {  // checked assets always stay visible
    const t = fin.value.trim().toLowerCase();
    list.querySelectorAll("label").forEach(lab => {
      lab.hidden = !lab.querySelector("input").checked && ((dataOnly && !lab.dataset.d) || (t && !lab.dataset.s.includes(t)));
    });
    list.querySelectorAll(".stacsel-grp").forEach(g => { g.hidden = !g.querySelector("label:not([hidden])"); });
    el.querySelectorAll(".stacsel-dt button").forEach(b => b.classList.toggle("on", !!b.dataset.d === dataOnly));
  }
  fin.addEventListener("input", filter);
  el.querySelectorAll(".stacsel-dt button").forEach(b => b.addEventListener("click", () => { dataOnly = !!b.dataset.d; filter(); }));
  function buildPars() {  // one control per opener param
    row.innerHTML = "";
    for (const [k, p] of Object.entries(model.get("params"))) {
      const lab = document.createElement("label"), inp = document.createElement(p.kind === "choice" ? "select" : "input");
      inp.dataset.k = k; lab.append(k, inp);
      if (p.kind === "choice") {
        for (const c of p.default == null ? ["", ...p.choices] : p.choices) inp.add(new Option(c || "default", c));
        inp.addEventListener("change", () => setValue(k, inp.value));
      } else {
        inp.type = p.kind === "str" ? "text" : "number"; inp.step = "any"; inp.placeholder = p.default ?? "";
        inp.addEventListener("input", () => setValue(k, inp.value));
      }
      row.append(lab);
    }
    q(".stacsel-pars").style.display = row.children.length ? "" : "none";
    sync();
  }
  function sync() {  // push model state into the controls (typing keeps its own value)
    const sel = model.get("assets"), vals = model.get("values");
    list.querySelectorAll("input").forEach(cb => { cb.checked = sel.includes(cb.value); });
    q(".clr").disabled = !sel.length; filter();
    row.querySelectorAll("[data-k]").forEach(inp => { if (inp !== document.activeElement) inp.value = vals[inp.dataset.k] ?? ""; });
    code.textContent = model.get("code"); err.textContent = model.get("error");
  }
  copyBtn(el, q(".stacsel-cp"), () => code.textContent);
  q(".clr").addEventListener("click", () => setAssets([]));
  model.on("change:asset_options", buildAssets); model.on("change:params", buildPars);
  for (const k of ["assets", "values", "code", "error"]) model.on("change:" + k, sync);
  buildAssets(); buildPars();
}
export default { render };
"""

_WIDGET_CSS = _ROOT + """
.stacsel .sg{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:20px}
.stacsel-map{min-height:340px;border:1px solid var(--sv-border);border-radius:3px;position:relative}
@container (max-width:720px){.stacsel .sg{grid-template-columns:minmax(0,1fr)}.stacsel-map{aspect-ratio:16/10;min-height:240px}}
.stacsel-tools{position:absolute;z-index:500;top:10px;left:54px;right:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;pointer-events:none}
.stacsel-tools>*{pointer-events:auto}
.stacsel-hint{font-size:11px;color:var(--sv-muted);background:color-mix(in srgb,var(--sv-bg) 85%,transparent);padding:2px 6px;border-radius:3px;pointer-events:none}
.stacsel-seg{display:inline-flex;border:1px solid var(--sv-border-strong);border-radius:4px;overflow:hidden;background:var(--sv-bg)}
.stacsel-seg button{font:inherit;font-size:12px;color:var(--sv-fg);background:none;border:0;padding:4px 10px;cursor:pointer}
.stacsel-seg button+button{border-left:1px solid var(--sv-border-strong)}
.stacsel-seg button.on{background:var(--sv-accent);color:#fff}.stacsel-seg button:hover:not(.on,:disabled){background:var(--sv-alt)}
.stacsel-seg button:disabled{color:var(--sv-faint);cursor:default}
.stacsel-panel{display:flex;flex-direction:column;gap:14px;min-width:0}
.stacsel-row{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}
.stacsel label{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--sv-muted)}
.stacsel input[type=date],.stacsel input[type=text],.stacsel input[type=number],.stacsel select{""" + _IN + """}
.stacsel input[type=date]:focus,.stacsel input[type=text]:focus,.stacsel input[type=number]:focus,.stacsel select:focus{""" + _IN_FOCUS + """}
.stacsel input[type=date].bad{border-color:var(--sv-err)}
.stacsel-row .stacsel-span{margin-left:auto;font-size:12px;color:var(--sv-muted);padding-bottom:5px;min-width:56px;text-align:right}
.stacsel-sl{position:relative;height:18px;margin-top:12px}
.stacsel-sl::before{content:"";position:absolute;left:7px;right:7px;top:7px;height:4px;border-radius:2px;background:var(--sv-alt)}
.stacsel-fill{position:absolute;top:7px;height:4px;border-radius:2px;background:var(--sv-accent)}
.stacsel-sl input{position:absolute;left:0;top:0;width:100%;height:18px;margin:0;padding:0;background:none;
  pointer-events:none;-webkit-appearance:none;appearance:none}
.stacsel-sl input::-webkit-slider-runnable-track{height:18px;background:none}
.stacsel-sl input::-moz-range-track{height:18px;background:none}
.stacsel-sl input::-webkit-slider-thumb{-webkit-appearance:none;pointer-events:auto;box-sizing:border-box;width:14px;height:14px;
  margin-top:2px;border-radius:50%;background:var(--sv-bg);border:2px solid var(--sv-accent);cursor:pointer}
.stacsel-sl input::-moz-range-thumb{pointer-events:auto;box-sizing:border-box;width:14px;height:14px;
  border-radius:50%;background:var(--sv-bg);border:2px solid var(--sv-accent);cursor:pointer}
.stacsel-tk{display:flex;justify-content:space-between;font-size:11px;color:var(--sv-faint);margin-top:2px}
.stacsel-pre{margin-top:12px}
.stacsel-opts label.cc{flex:1 1 180px}
.stacsel input.cc{-webkit-appearance:none;appearance:none;width:100%;height:18px;margin:4px 0 5px;padding:0;background:none;cursor:pointer}
.stacsel input.cc::-webkit-slider-runnable-track{height:4px;border-radius:2px;
  background:linear-gradient(to right,var(--sv-accent) var(--p,100%),var(--sv-alt) var(--p,100%))}
.stacsel input.cc::-moz-range-track{height:4px;border-radius:2px;background:var(--sv-alt)}
.stacsel input.cc::-moz-range-progress{height:4px;border-radius:2px;background:var(--sv-accent)}
.stacsel input.cc::-webkit-slider-thumb{-webkit-appearance:none;box-sizing:border-box;width:14px;height:14px;margin-top:-5px;
  border-radius:50%;background:var(--sv-bg);border:2px solid var(--sv-accent)}
.stacsel input.cc::-moz-range-thumb{box-sizing:border-box;width:14px;height:14px;border-radius:50%;background:var(--sv-bg);border:2px solid var(--sv-accent)}
.stacsel input.cc:focus-visible{outline:none}.stacsel input.cc:focus-visible::-webkit-slider-thumb{box-shadow:0 0 0 3px color-mix(in srgb,var(--sv-accent) 30%,transparent)}
.stacsel-cw{position:relative}
.stacsel-oc>summary{cursor:pointer;list-style:none;width:fit-content}.stacsel-oc>summary::-webkit-details-marker{display:none}
.stacsel-oc>summary::before{content:"\\25B8";display:inline-block;margin-right:5px;transition:transform .15s}.stacsel-oc[open]>summary::before{transform:rotate(90deg)}
.stacsel-oc:not([open])>summary{margin:0}
.stacsel-cp{position:absolute;top:6px;right:6px;display:inline-flex;padding:4px;border:1px solid var(--sv-border);border-radius:3px;
  background:var(--sv-bg);color:var(--sv-muted);cursor:pointer;opacity:.7}
.stacsel-cp svg{width:14px;height:14px}.stacsel-cw:hover .stacsel-cp,.stacsel-cp:focus-visible{opacity:1}
.stacsel-cp:hover,.stacsel-cp.ok{color:var(--sv-accent);border-color:var(--sv-accent)}
.stacsel-sels{display:contents}
.stacsel-atb{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}.stacsel-atb [hidden]{display:none}
.stacsel-af{position:relative;display:flex;align-items:center;flex:0 1 260px;color:var(--sv-muted)}
.stacsel-af svg{position:absolute;left:8px;width:13px;height:13px;pointer-events:none}
.stacsel-af input{""" + _IN + """;width:100%;padding-left:28px}.stacsel-af input:focus{""" + _IN_FOCUS + """}
.stacsel-af input::placeholder{color:var(--sv-faint)}
.stacsel-assets{display:flex;flex-direction:column;gap:8px;max-height:240px;overflow:auto;padding-right:4px}
.stacsel-grp{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:3px 16px}.stacsel-grp>.stacsel-gh{grid-column:1/-1}
.stacsel-assets [hidden]{display:none}
.stacsel-gh{display:block;font:inherit;font-size:11px;color:var(--sv-muted);margin-bottom:2px;padding:0;background:none;border:0;text-align:left}
button.stacsel-gh{cursor:pointer}button.stacsel-gh:hover{color:var(--sv-accent);text-decoration:underline}
.stacsel-bn{color:var(--sv-faint);font-size:11px}
.stacsel-assets label{flex-direction:row;align-items:center;gap:6px;font-size:12px;color:var(--sv-fg)}
.stacsel-assets label:has(:disabled){color:var(--sv-faint)}
.stacsel-err{color:var(--sv-err);font-size:12px;margin-top:6px}.stacsel-err:empty{display:none}
.stacsel-code{margin:0;padding:8px 10px;background:var(--sv-code-bg);border:1px solid var(--sv-border);border-radius:2px;
  font:12px/1.5 var(--sv-mono);white-space:pre-wrap;overflow-wrap:anywhere;color:var(--sv-fg)}
"""


# ------------------------------------------------------ collection overview filter
# rows whose data-q contains every whitespace-separated term stay visible
_FILTER_JS = """  // needs `el` (the .sc card) and CHECK in scope
const box = el.querySelector(".q"), inp = box.querySelector("input");
const count = box.querySelector("span"), rows = [...el.querySelectorAll("tr[data-q]")];
box.hidden = false;
inp.oninput = () => {
  const terms = inp.value.toLowerCase().split(/\\s+/).filter(Boolean);
  let n = 0;
  for (const r of rows) { const on = terms.every(t => r.dataset.q.includes(t)); r.hidden = !on; n += on; }
  count.textContent = terms.length ? n + " of " + rows.length : "";
};
inp.onkeydown = e => { if (e.key === "Escape" && inp.value) { inp.value = ""; inp.oninput(); } };
const copy = s => navigator.clipboard ? navigator.clipboard.writeText(s) : Promise.reject();
const fallback = s => {  // clipboard API can be blocked (e.g. VS Code output iframes)
  const t = document.createElement("textarea"); t.value = s; el.append(t); t.select(); document.execCommand("copy"); t.remove(); };
for (const b of el.querySelectorAll(".cp")) {
  b.hidden = false;
  b.onclick = async () => {
    try { await copy(b.dataset.id); } catch { fallback(b.dataset.id); }
    const svg = b.innerHTML; b.innerHTML = CHECK; b.classList.add("ok");
    setTimeout(() => { b.innerHTML = svg; b.classList.remove("ok"); }, 1200);
  };
}
// styled hover card instead of the native title tooltip (kept as the no-JS fallback)
const tip = document.createElement("div"), tb = document.createElement("b"), td = document.createElement("div");
const im = document.createElement("img");
tip.className = "tip"; tip.hidden = true; tip.append(im, tb, td); el.append(tip);
let last;  // last mouse event, to re-place the card once the image size is known
im.onload = () => last && place(last);
im.onerror = () => { im.hidden = true; };
const view = () => {  // visible area: the window cut by every clipping ancestor (e.g. the notebook's scroll panel)
  let v = { top: 0, left: 0, right: innerWidth, bottom: innerHeight };
  for (let p = el.parentElement; p; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (/auto|scroll|hidden|clip/.test(s.overflowY + s.overflowX)) {
      const b = p.getBoundingClientRect();
      v = { top: Math.max(v.top, b.top), left: Math.max(v.left, b.left), right: Math.min(v.right, b.right), bottom: Math.min(v.bottom, b.bottom) };
    }
  }
  return v;
};
const place = e => {
  last = e;
  const r = tip.getBoundingClientRect(), v = view(), o = el.getBoundingClientRect();  // the card is the containing block (container-type)
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + r.width > v.right - 8) x = Math.max(v.left + 8, v.right - r.width - 8);
  if (y + r.height > v.bottom - 8) y = Math.max(v.top + 8, e.clientY - r.height - 10);  // flip above the cursor, never past the top
  tip.style.left = x - o.left + "px";
  tip.style.top = y - o.top + "px";
};
for (const r of rows) {
  const [t, ...d] = r.title.split("\\n\\n");
  r.removeAttribute("title");
  r.onmouseenter = e => {
    tb.textContent = t; td.textContent = d.join(" "); td.hidden = !d.length;
    const src = r.dataset.img || "";  // loaded only on hover
    im.hidden = !src; if (src && im.getAttribute("src") !== src) im.src = src; else if (!src) im.removeAttribute("src");
    tip.hidden = false; place(e);
  };
  r.onmousemove = place;
  r.onmouseleave = () => { tip.hidden = true; };
}
"""

# catalog overview as a widget: clicking a row sets `selected` (the collection's attribute name)
_PICK_ESM = f"const CHECK = {json.dumps(_CHECK_SVG)};" + """
function render({ model, el: host }) {
  host.innerHTML = model.get("html");
  const el = host.querySelector(".sc");
  el.classList.add("pick");
""" + _FILTER_JS + """
  const mark = () => { for (const r of rows) r.classList.toggle("sel", r.dataset.k === model.get("selected")); };
  for (const r of rows) r.onclick = e => {
    if (e.target.closest(".cp")) return;
    model.set("selected", r.dataset.k); model.save_changes();
  };
  model.on("change:selected", mark); mark();
  return () => model.off("change:selected", mark);
}
export default { render };
"""

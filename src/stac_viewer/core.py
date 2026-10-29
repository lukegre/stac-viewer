"""stac_viewer: browse a STAC catalog with dot notation and rich notebook viewers.

Usage (Jupyter / JupyterLab / VS Code / Colab)::

    from stac_viewer import browse

    cat = browse("https://earth-search.aws.element84.com/v1")  # API URL, catalog.json path/URL or pystac object
    cat                                   # overview table of collections
    cat.sentinel_2_l2a                    # HTML viewer: map, temporal bar, metadata
    sel = cat.sentinel_2_l2a.selector()   # interactive bbox + date picker (needs anywidget); show it with `sel`
    items = cat.sentinel_2_l2a.search(sel)  # search using the selection -> Items
    items = cat.sentinel_2_l2a.search(bbox=[8.4, 47.3, 8.7, 47.5], datetime="2026-06-01/2026-06-30", max_items=50)
    items                                 # HTML: count, map of item footprints, first rows

    browse(url, modifier=planetary_computer.sign_inplace)  # extra kwargs go to pystac_client.Client.open
    browse(url, refresh=True)             # ignore the collection cache (refreshed anyway after 5 days)

Dependencies: pystac, pystac-client; anywidget (optional, only for ``selector()``).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import itertools
import json
import keyword
import math
import operator
import os
import re
import time
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator

import pystac
from pystac_client import Client
from pystac_client._utils import call_modifier
from pystac_client.collection_client import CollectionClient
from pystac_client.conformance import ConformanceClasses
from pystac_client.exceptions import APIError

try:  # optional: interactive selector
    import anywidget
    import traitlets
except ImportError:  # pragma: no cover
    anywidget = None
    traitlets = None

__all__ = ["browse", "Collections", "CollectionView", "Items", "SearchSelector"]

_LAND = files("stac_viewer").joinpath("land.txt").read_text()  # SVG path, 0..360 x 0..180 (x=lon+180, y=90-lat)
_UTC = dt.timezone.utc
CACHE_MAX_AGE = dt.timedelta(days=5)
_PAGE_LIMIT = 1000  # /collections page size; servers cap it at their own maximum
_FILTER_KEYS = ("platform", "constellation", "sat:orbit_state", "sar:instrument_mode", "sar:product_type")


# --------------------------------------------------------------------------- helpers
def _collection_pages(client: Client) -> Iterator[dict]:
    """Raw ``/collections`` pages, asking for large pages to cut round trips."""
    io, url = client._stac_io, client._collections_href()
    pages = io.get_pages(url, parameters={"limit": _PAGE_LIMIT})
    try:
        first = next(pages, None)
    except APIError:  # limit rejected: fall back to the server's default page size
        pages = io.get_pages(url)
        first = next(pages, None)
    return itertools.chain([first] if first else [], pages)


def _ident(raw: str, taken: set[str]) -> str:
    """Turn a STAC id into a unique, valid Python identifier."""
    key = re.sub(r"\W", "_", raw) or "_"
    if key[0].isdigit():
        key = "_" + key
    if keyword.iskeyword(key):
        key += "_"
    base, i = key, 2
    while key in taken:
        key, i = f"{base}_{i}", i + 1
    return key


def _bbox4(b) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) from a 4- or 6-element bbox."""
    return (b[0], b[1], b[3], b[4]) if len(b) == 6 else tuple(b[:4])  # type: ignore[return-value]


def _day(d: dt.datetime | None) -> str:
    return d.strftime("%Y-%m-%d") if d else ""


def _stamp(d: dt.datetime) -> str:
    """'YYYY-MM-DD HH:MM:SS UTC' (naive datetimes are taken as UTC)."""
    d = d.replace(tzinfo=_UTC) if d.tzinfo is None else d.astimezone(_UTC)
    return d.strftime("%Y-%m-%d %H:%M:%S UTC")


def _extent_defaults(col: pystac.Collection) -> tuple[list[float], str, str]:
    """Collection extent as (bbox, start 'YYYY-MM-DD', end 'YYYY-MM-DD')."""
    bboxes = col.extent.spatial.bboxes or [[-180, -90, 180, 90]]
    bbox = [float(v) for v in _bbox4(bboxes[0])]
    interval = (col.extent.temporal.intervals or [[None, None]])[0]
    start = _day(interval[0])
    end = _day(interval[1]) or dt.datetime.now(_UTC).strftime("%Y-%m-%d")
    return bbox, start, end


# ------------------------------------------------------------- datetime handling
def _to_dt(v, end: bool = False) -> dt.datetime | None:
    """str / datetime / date / None -> aware datetime (date-only: start or end of day)."""
    if v is None or v == "" or v == "..":
        return None
    if isinstance(v, str):
        v = v.strip()
        v = dt.date.fromisoformat(v) if len(v) == 10 else dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if not isinstance(v, dt.datetime):  # plain date
        v = dt.datetime.combine(v, dt.time.max if end else dt.time.min)
    return v if v.tzinfo else v.replace(tzinfo=_UTC)


def _dt_range(value) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Normalise 'a/b', 'a', '../b', a datetime/date, or a 2-tuple of those to (start, end)."""
    if value is None:
        return None, None
    if isinstance(value, str):
        a, sep, b = value.partition("/")
        value = (a, b) if sep else (a, a)
    elif not isinstance(value, (tuple, list)):
        value = (value, value)
    a, b = value
    return _to_dt(a), _to_dt(b, end=True)


def _item_span(item: pystac.Item) -> tuple[dt.datetime | None, dt.datetime | None]:
    p = item.properties
    return item.datetime or _to_dt(p.get("start_datetime")), item.datetime or _to_dt(p.get("end_datetime"), end=True)


# ------------------------------------------------------------- client-side search

_OPS = {"lt": operator.lt, "lte": operator.le, "gt": operator.gt, "gte": operator.ge, "eq": operator.eq, "neq": operator.ne}


def _coords(c) -> list:
    """Flatten nested GeoJSON coordinates to a list of positions."""
    return [c] if isinstance(c[0], (int, float)) else [p for x in c for p in _coords(x)]


def _local_search(col: pystac.Collection, bbox=None, datetime=None, ids=None, max_items=None, intersects=None,
                  query=None, **kwargs):
    """Minimal filter for static catalogs (no STAC API available).

    ``intersects`` is approximated by the bounding box of the geometry's coordinates.
    ``query`` supports lt, lte, gt, gte, eq, neq on item properties; items lacking the property are excluded.
    """
    if kwargs:
        raise TypeError(
            f"unsupported kwargs {', '.join(kwargs)}: static catalogs only support "
            "bbox, intersects, datetime, ids, query, max_items"
        )
    t0, t1 = _dt_range(datetime)
    boxes = [_bbox4(bbox)] if bbox else []
    if intersects:
        xs, ys = zip(*(p[:2] for p in _coords(intersects["coordinates"])))
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    out = []
    for item in col.get_items(recursive=True):
        if ids and item.id not in ids:
            continue
        if item.bbox:
            w, s, e, n = _bbox4(item.bbox)
            if any(e < qw or w > qe or n < qs or s > qn for qw, qs, qe, qn in boxes):
                continue
        p = item.properties
        if any(k not in p or not all(_OPS[op](p[k], v) for op, v in conds.items()) for k, conds in (query or {}).items()):
            continue
        if t0 or t1:
            lo, hi = _item_span(item)
            if lo is None:
                continue
            if (t1 and lo > t1) or (t0 and (hi or lo) < t0):
                continue
        out.append(item)
        if max_items and len(out) >= max_items:
            break
    return out


# ------------------------------------------------------------------ HTML rendering
_CSS = """<style>
.sc{font:13px/1.4 var(--jp-ui-font-family,system-ui,sans-serif);border:1px solid rgba(128,128,128,.4);
 border-radius:8px;padding:10px;max-width:900px;color:var(--vscode-editor-foreground,inherit)}
.vscode-dark .sc,[data-vscode-theme-kind*=dark] .sc{color-scheme:dark}
.sc h4{margin:0 0 2px;font-size:15px}.sc .m{opacity:.65}
.sc details{overflow-x:auto;margin-top:6px;border-top:1px solid rgba(128,128,128,.25);padding-top:6px}
.sc summary{cursor:pointer;font-weight:600}
.sc table{border-collapse:collapse}.sc td{padding:1px 10px 1px 0;vertical-align:top}
.sc .row{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;margin-top:8px}
.sc .mp{flex:1 1 50%;min-width:50%}.sc .info{flex:1 1 40%;min-width:220px}
.sc .ds{white-space:pre-line}.sc details.dsc{border:0;padding:0;margin:0}
.sc .dsc>summary{font-weight:400}.sc .dsc[open]>summary .dmore{display:none}
.sc .dsc>summary .dless{display:none;font-size:11px;opacity:.6}.sc .dsc[open]>summary .dless{display:inline}
.sc .bar{position:relative;height:8px;background:rgba(128,128,128,.25);border-radius:4px;margin:6px 0 2px}
.sc .bar>div{position:absolute;top:0;bottom:0;background:#3b82f6;border-radius:4px}
.sc .nw{white-space:nowrap}
.sc .row2{margin-top:8px}.sc .row2 .il{margin-top:8px}.sc .il table{width:100%}
.sc .il{max-height:calc(16 * (1.4em + 2px));overflow:auto}.sc .il th{position:sticky;top:0;z-index:1;background:var(--vscode-editor-background,var(--jp-layout-color0,Canvas));text-align:left;font-weight:600}
.sc .il td{text-align:left}
.sc .il tr[data-i]{cursor:pointer}.sc .il tr.hov{background:rgba(59,130,246,.15)}
.sc .il tr.selected{background:rgba(59,130,246,.3);box-shadow:inset 3px 0 #f97316;font-weight:600}
.sc .pop{display:flex;gap:8px;align-items:flex-start}
.sc .pop img{display:block;flex:none;width:160px;max-height:160px;object-fit:contain}
.sc .pop>div{min-width:0;max-width:220px}
.sc .pop b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc .tk{display:flex;justify-content:space-between;font-size:11px;opacity:.6}
</style>"""


def _svg_map(bboxes, w: int = 240, h: int = 120, view=None) -> str:
    """Footprints on the land map; ``view`` = (west, south, east, north) window (default: whole world)."""
    west, south, east, north = view or (-180, -90, 180, 90)
    x = lambda lon: (lon - west) / (east - west) * w
    y = lambda lat: (north - lat) / (north - south) * h
    if view is None:
        xs, ys = range(-180, 181, 60), range(-90, 91, 30)
    else:
        def ticks(lo, hi):
            step = next((t for t in (0.1, 0.25, 0.5, 1, 2, 5, 10, 30) if (hi - lo) / t <= 5), 30)
            return [i * step for i in range(math.ceil(lo / step), math.floor(hi / step) + 1)]
        xs, ys = ticks(west, east), ticks(south, north)
    grid = "".join(f'<line x1="{x(v):.1f}" y1="0" x2="{x(v):.1f}" y2="{h}"/>' for v in xs)
    grid += "".join(f'<line x1="0" y1="{y(v):.1f}" x2="{w}" y2="{y(v):.1f}"/>' for v in ys)
    rects = ""
    for i, b in enumerate(bboxes):
        bw, s, be, n = _bbox4(b)
        spans = [(bw, be)] if bw <= be else [(bw, 180), (-180, be)]  # antimeridian
        for a, c in spans:
            rects += (
                f'<rect x="{x(a):.1f}" y="{y(n):.1f}" width="{max(x(c) - x(a), 2):.1f}" '
                f'height="{max(y(s) - y(n), 2):.1f}" fill="#3b82f6" fill-opacity="{.35 if i == 0 else .2}" '
                f'stroke="#1d4ed8"><title>[{bw:.2f}, {s:.2f}, {be:.2f}, {n:.2f}]</title></rect>'
            )
    sx, sy = w / (east - west), h / (north - south)
    move = "" if view is None else f"translate({-(west + 180) * sx:.4f}, {-(90 - north) * sy:.4f}) "
    land = f'<path d="{_LAND}" transform="{move}scale({sx:.4f}, {sy:.4f})" fill="rgba(128,128,128,.28)"/>'
    clip = "" if view is None else ";overflow:hidden"
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="border:1px solid rgba(128,128,128,.4){clip}">{land}'
        f'<g stroke="rgba(128,128,128,.35)" stroke-width="1">{grid}</g>{rects}</svg>'
    )


def _items_view(bboxes, aspect: float = 2.0):
    """Map window around the union of bboxes: padded 50% (min 0.5 deg), aspect-fixed, clamped to the world."""
    bs = [_bbox4(b) for b in bboxes]
    bs = [b for b in bs if b[0] <= b[2]]  # skip antimeridian crossers
    if not bs:
        return None
    w0, s0, e0, n0 = min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)
    pw, ph = max((e0 - w0) * .5, .5), max((n0 - s0) * .5, .5)
    w0, e0, s0, n0 = w0 - pw, e0 + pw, s0 - ph, n0 + ph
    cx, cy = (w0 + e0) / 2, (s0 + n0) / 2
    hw = max((e0 - w0) / 2, (n0 - s0) / 2 * aspect)  # half-width keeping width = aspect * height
    hw = min(hw, 180)
    hh = hw / aspect
    cx, cy = min(max(cx, -180 + hw), 180 - hw), min(max(cy, -90 + hh), 90 - hh)
    return cx - hw, cy - hh, cx + hw, cy + hh


def _bar_span(t0, t1, now=None) -> tuple[int, float, float]:
    """Temporal bar on a fixed axis (1980 or earlier -> today): (axis start year, left %, width %)."""
    now = now or dt.datetime.now(_UTC)
    a0 = min(dt.datetime(1980, 1, 1, tzinfo=_UTC), t0) if t0 else dt.datetime(1980, 1, 1, tzinfo=_UTC)
    pct = lambda d: min(max((d - a0) / (now - a0) * 100, 0), 100)
    left, right = pct(t0 or a0), pct(t1 or now)
    return a0.year, left, right - left


def _table(d: dict) -> str:
    rows = "".join(
        f"<tr><td class='m'>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in d.items()
    )
    return f"<table>{rows}</table>"


_MEDIA = {
    "image/tiff; application=geotiff; profile=cloud-optimized": "COG",
    "image/tiff; application=geotiff": "GeoTIFF",
    "image/tiff": "TIFF",
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/webp": "WebP",
    "image/gif": "GIF",
    "application/x-netcdf": "NetCDF",
    "application/netcdf": "NetCDF",
    "application/x-hdf": "HDF",
    "application/x-hdf5": "HDF5",
    "application/vnd+zarr": "Zarr",
    "application/x-parquet": "Parquet",
    "application/vnd.apache.parquet": "Parquet",
    "application/wmo-grib2": "GRIB2",
    "application/vnd.laszip+copc": "COPC",
    "application/geopackage+sqlite3": "GeoPackage",
    "application/geo+json": "GeoJSON",
    "application/json": "JSON",
    "application/x-ndjson": "NDJSON",
    "application/xml": "XML",
    "text/xml": "XML",
    "application/zip": "ZIP",
    "application/pdf": "PDF",
    "text/plain": "text",
    "text/html": "HTML",
}


def _media(t: str | None) -> str:
    """Short label for a media type ('COG', 'NetCDF', ...), else the media type itself."""
    if not t:
        return ""
    return _MEDIA.get(t.lower().replace(" ", "").replace(";", "; "), t)


def _assets_html(label: str, assets: dict[str, dict], href: bool = False) -> str:
    """<details> table of asset key, title (or href) and short media type; the summary lists the formats."""
    rows, kinds = [], []
    for k, a in assets.items():
        t = a.get("type") or ""
        kinds.append(_media(t))
        rows.append(
            f"<tr><td class='m'>{html.escape(str(k))}</td>"
            f"<td class='nw' title='{html.escape(t, quote=True)}'>{html.escape(_media(t))}</td>"
            f"<td>{html.escape(str(a.get('href' if href else 'title') or ''))}</td></tr>"
        )
    kinds = ", ".join(dict.fromkeys(k for k in kinds if k))
    kinds = f" <span class='m' style='font-weight:400'>· {html.escape(kinds)}</span>" if kinds else ""
    return f"<details><summary>{label} ({len(rows)}){kinds}</summary><table>{''.join(rows)}</table></details>"


def _desc_html(text: str | None, n: int = 300) -> str:
    """Description in full if short, else a <details> whose summary is the first ~n chars (word boundary)."""
    text = (text or "").strip()
    if len(text) <= n:
        return f"<div class='m ds'>{html.escape(text)}</div>"
    head = text[:n]
    if not text[n].isspace() and len(head.split()) > 1:
        head = head.rsplit(None, 1)[0]
    return (
        f"<details class='dsc ds m'><summary><span class='dmore'>{html.escape(head.rstrip())}&hellip; more</span>"
        f"<span class='dless'>show less</span></summary>"
        f"{html.escape(text)}</details>"
    )


# ------------------------------------------------------------------ Leaflet maps
_TILE_BASE = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
_TILE_LABELS = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
_TILE_ATTR = "&copy; Esri, OSM"
_LEAFLET_JS = f"const TILE_BASE = {json.dumps(_TILE_BASE)}, TILE_LABELS = {json.dumps(_TILE_LABELS)}, TILE_ATTR = {json.dumps(_TILE_ATTR)}, TILE_MAX = 16;\n" + """
const LEAF = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/";
async function leaflet() {  // CSS link once per page; dynamic import avoids RequireJS clashes
  if (!document.getElementById("stacmap-css")) {
    const l = document.createElement("link");
    l.id = "stacmap-css"; l.rel = "stylesheet"; l.href = LEAF + "leaflet.css";
    await new Promise(r => { l.onload = l.onerror = r; document.head.append(l); });
  }
  if (!document.getElementById("stacmap-style")) {  // small attribution
    const st = document.createElement("style");
    st.id = "stacmap-style";
    st.textContent = ".leaflet-container .leaflet-control-attribution{font-size:9px;line-height:1.2;padding:0 4px;background:rgba(255,255,255,.6)}";
    document.head.append(st);
  }
  return import(LEAF + "leaflet-src.esm.js");
}
function basemap(L, el) {
  const map = L.map(el, { scrollWheelZoom: false, worldCopyJump: true, maxZoom: TILE_MAX });
  map.attributionControl.setPrefix(false);
  L.tileLayer(TILE_BASE, { maxZoom: TILE_MAX, attribution: TILE_ATTR }).addTo(map);
  L.tileLayer(TILE_LABELS, { maxZoom: TILE_MAX }).addTo(map);  // labels on top of the base
  return map;
}
"""

# body of the async IIFE in the static cards; `id` and `data` ({bboxes?, features?}) are defined around it
_MAP_JS = """
const el = document.getElementById(id), fallback = el.innerHTML;
let map, ro;
try {
  const L = await leaflet();
  el.innerHTML = ""; el.style.height = "260px";
  map = basemap(L, el);
  const tip = s => { const d = document.createElement("div"); d.textContent = s; return d; };  // no innerHTML
  const group = L.featureGroup().addTo(map);
  const style = { color: "#1d4ed8", weight: 1, fillColor: "#3b82f6" };
  const bboxes = data.bboxes || [];
  bboxes.forEach((b, i) => {
    const spans = b[0] <= b[2] ? [[b[0], b[2]]] : [[b[0], 180], [-180, b[2]]];  // antimeridian
    for (const [a, c] of spans)
      L.rectangle([[b[1], a], [b[3], c]], { ...style, fillOpacity: i ? 0.2 : 0.35 })
        .bindTooltip(tip("[" + b.map(v => v.toFixed(2)).join(", ") + "]")).addTo(group);
  });
  if (data.features) {
    const base = { ...style, opacity: 1, fillOpacity: 0.25 }, hl = { color: "#f97316", weight: 2, opacity: 1, fillOpacity: 0.45 };
    const layers = [], rows = {}, http = u => /^https?:\\/\\//i.test(u || "");
    (el.closest(".sc") || document).querySelectorAll("tr[data-i]").forEach(r => rows[r.dataset.i] = r);  // list rows, if any
    const row = (i, c, on) => rows[i] && rows[i].classList.toggle(c, on);
    const dim = { opacity: 0.25, fillOpacity: 0.06 };
    let sel, hv;  // selected (clicked) and hovered item
    const paint = () => layers.forEach((l, j) => l.setStyle(j === sel || j === hv ? hl : hv != null ? dim : base));
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
      box.appendChild(d); add("b", p.id).title = p.id; add("div", p.time);
      for (const [k, v] of Object.entries(p.props || {})) add("div", k + ": " + v);
      if (http(p.href)) { const a = add("a", "item JSON"); a.href = p.href; a.target = "_blank"; a.rel = "noopener"; }
      pop.setLatLng(layers[i].getBounds().getCenter()).setContent(box).openOn(map);  // closes the previous popup first
      sel = i; row(i, "selected", true); paint();
      const r = rows[i], c = r && r.closest(".il");
      if (scroll && c) c.scrollTop += r.getBoundingClientRect().top - c.getBoundingClientRect().top - (c.clientHeight - r.offsetHeight) / 2;  // list only, not the page
    };
    map.on("popupclose", () => { if (sel != null) row(sel, "selected", false); sel = null; paint(); });
    L.geoJSON(data.features, { style: base, onEachFeature: (f, l) => {
      const i = f.properties.i; layers[i] = l;
      l.bindTooltip(tip(f.properties.t)).on({ mouseover: () => hover(i, true), mouseout: () => hover(i, false), click: () => open(i, true) });
      if (rows[i]) rows[i].onmouseenter = () => hover(i, true), rows[i].onmouseleave = () => hover(i, false), rows[i].onclick = () => open(i);
    } }).addTo(group);
  }
  const world = bboxes.some(b => b[2] - b[0] >= 350 && b[3] - b[1] >= 170);
  let fitted = false;
  const fit = () => { map.fitBounds(world ? [[-90, -180], [90, 180]] : group.getBounds(), { padding: [12, 12] }); fitted = true; };
  if (el.clientWidth > 0) fit();  // zero-size (hidden) containers would zoom to max, so wait for a size
  ro = new ResizeObserver(() => { map.invalidateSize(); if (!fitted && el.clientWidth > 0) fit(); });
  ro.observe(el);
} catch (err) {  // keep the SVG fallback
  if (ro) ro.disconnect();
  if (map) map.remove();
  el.innerHTML = fallback; el.style.height = "";
}
"""


_POP_PROPS = ("platform", "constellation", "gsd", "proj:epsg", "sat:orbit_state")


def _is_http(u: str) -> bool:
    return u.lower().startswith(("http://", "https://"))


def _thumb(item: pystac.Item) -> str | None:
    """Thumbnail URL: first asset with role 'thumbnail', else a well-known key; http(s) and not a TIFF."""
    a = next((a for a in item.assets.values() if "thumbnail" in (a.roles or [])), None)
    a = a or next((item.assets[k] for k in ("thumbnail", "rendered_preview", "preview") if k in item.assets), None)
    if a and _is_http(a.href) and (a.media_type or "image/png") in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        return a.href


def _json(x) -> str:
    """JSON safe to embed in a <script> tag."""
    return json.dumps(x).replace("</", "<\\/").replace("<!--", "<\\!--")


def _map_html(data: dict, fallback_svg: str) -> str:
    """Container holding the SVG fallback, plus a script that swaps in a Leaflet map when JS can run."""
    uid = f"stacmap-{uuid.uuid4().hex}"
    return (
        f'<div id="{uid}">{fallback_svg}</div>'
        f"<script>(async()=>{{const id={_json(uid)},data={_json(data)};{_LEAFLET_JS}{_MAP_JS}}})()</script>"
    )


# ------------------------------------------------------------ interactive selector
_ESM = _LEAFLET_JS + """
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
  el.innerHTML = `
    <div class="stacsel-bar">
      <button type="button" class="stacsel-draw bb">Draw bbox</button>
      <button type="button" class="stacsel-draw pg">Draw polygon</button>
      <button type="button" class="stacsel-draw sm rs">Reset</button>
      <span class="stacsel-hint"></span>
    </div>
    <div class="stacsel-map"></div>
    <div class="stacsel-row">
      <label>Start<input type="date" class="d0"></label>
      <label>End<input type="date" class="d1"></label>
      <span class="stacsel-span"></span>
    </div>
    <div class="stacsel-sl"><div class="stacsel-fill"></div><input type="range" class="r0" min="0" step="1"><input type="range" class="r1" min="0" step="1"></div>
    <div class="stacsel-tk"><span class="t0"></span><span class="t1"></span></div>
    <div class="stacsel-pre">
      <button type="button" class="stacsel-draw sm" data-n="7">Last 7d</button>
      <button type="button" class="stacsel-draw sm" data-n="30">30d</button>
      <button type="button" class="stacsel-draw sm" data-n="365">1y</button>
      <button type="button" class="stacsel-draw sm" data-n="0">All</button>
    </div>
    <div class="stacsel-opts">
      <label class="cc">Max cloud cover<input type="range" class="cc" min="0" max="100" step="5"><span class="ccv"></span></label>
      <span class="stacsel-sels"></span>
    </div>
    <div class="stacsel-err"></div>
    <pre class="stacsel-code"></pre>`;
  const q = s => el.querySelector(s);
  const err = q(".stacsel-err"), bb = q(".bb"), pg = q(".pg"), hint = q(".stacsel-hint");
  const d0 = q(".d0"), d1 = q(".d1"), r0 = q(".r0"), r1 = q(".r1"), cc = q("input.cc"), sels = q(".stacsel-sels");
  const fill = q(".stacsel-fill"), span = q(".stacsel-span");
  const axis = () => {  // slider axis: min_date (or 1980) .. max_date (or today), in days
    const mn = model.get("min_date") || "1980-01-01", mx = model.get("max_date") || ymd(Date.now());
    return [mn, mx, Math.max((ms(mx) - ms(mn)) / DAY, 1)];
  };
  const code = q(".stacsel-code"), mapEl = q(".stacsel-map");
  const pt = p => "[" + p.map(v => v.toFixed(2)).join(", ") + "]";
  let ext, sel, pl, map, mode = "";  // rectangles + polygon, created once the map is up
  function draw() {
    const b = model.get("bbox"), poly = model.get("polygon"), cloud = model.get("cloud_cover"), flt = model.get("filters");
    if (sel) {
      ext.setBounds(rb(model.get("extent"))); sel.setBounds(rb(b));
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
    cc.value = cloud; q(".ccv").textContent = cloud >= 100 ? "any" : `\\u2264 ${cloud}%`;
    sels.querySelectorAll("select").forEach(s => { s.value = flt[s.dataset.k] || ""; });
    const query = {};
    if (cloud < 100) query["eo:cloud_cover"] = { lte: cloud };
    for (const [k, v] of Object.entries(flt)) query[k] = { eq: v };
    const where = poly.length
      ? `intersects={"type": "Polygon", "coordinates": [[${poly.concat([poly[0]]).map(pt).join(", ")}]]}`
      : `bbox=${pt(b)}`;
    code.textContent = `search(\\n  ${where},\\n  datetime="${d0.value}/${d1.value}",${Object.keys(query).length ? `\\n  query=${JSON.stringify(query)},` : ""}\\n)`;
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
  function buildSels() {  // one <select> per filter option
    sels.innerHTML = "";
    for (const [k, vals] of Object.entries(model.get("filter_options"))) {
      const lab = document.createElement("label"), s = document.createElement("select");
      lab.append(k.split(":").pop(), s); s.dataset.k = k;
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

  const L = await leaflet();
  map = basemap(L, mapEl);
  const none = { interactive: false }, style = { ...none, color: "#1d4ed8", weight: 1, fillColor: "#3b82f6", fillOpacity: 0.3 };
  ext = L.rectangle(rb(model.get("extent")), { ...none, color: "#6b7280", weight: 1, dashArray: "4 3", fill: false }).addTo(map);
  sel = L.rectangle(rb(model.get("bbox")), style).addTo(map);
  pl = L.polygon([], style);
  const tmp = L.layerGroup().addTo(map), rubber = L.polyline([], { ...none, color: "#1d4ed8", weight: 1.5, dashArray: "4 3" });
  draw();
  let fitted = false;
  const fit = () => { map.fitBounds(ext.getBounds(), { padding: [10, 10] }); fitted = true; };
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
    if (verts.length === 1) L.circleMarker(e.latlng, { ...none, radius: 5, color: "#1d4ed8", fillOpacity: 1 }).addTo(tmp);
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

_WIDGET_CSS = """
.stacsel{font:13px/1.4 var(--jp-ui-font-family,system-ui,sans-serif);border:1px solid rgba(128,128,128,.4);
  border-radius:8px;padding:10px;max-width:640px;color:var(--vscode-editor-foreground,inherit)}
.vscode-dark .stacsel,[data-vscode-theme-kind*=dark] .stacsel{color-scheme:dark}  /* VS Code's widget host keeps light --jp-* colours */
.stacsel-bar{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}
.stacsel-draw{cursor:pointer;font:inherit;padding:2px 10px;border:1px solid rgba(128,128,128,.6);
  border-radius:6px;background:transparent;color:inherit}
.stacsel-draw.on{background:#3b82f6;border-color:#1d4ed8;color:#fff}
.stacsel-hint{font-size:11px;opacity:.6}
.stacsel-map{height:300px;border:1px solid rgba(128,128,128,.4);border-radius:4px}
.stacsel input[type=date],.stacsel select{font:inherit;color:inherit;background:transparent;box-sizing:border-box;height:26px;
  border:1px solid rgba(128,128,128,.6);border-radius:6px;padding:2px 6px;color-scheme:light dark}
.stacsel input[type=date]:focus-visible,.stacsel select:focus-visible{outline:2px solid #3b82f6;outline-offset:1px}
.stacsel input[type=date].bad{border-color:#dc2626}
.stacsel-row{display:flex;gap:12px;margin-top:8px;align-items:center;flex-wrap:wrap}
.stacsel-row label,.stacsel-opts label{display:flex;align-items:center;gap:6px;font-size:12px}
.stacsel-row .stacsel-span{margin-left:auto;font-size:12px;opacity:.7;min-width:56px;text-align:right}
.stacsel-sl{position:relative;height:20px;margin-top:6px}
.stacsel-sl::before{content:"";position:absolute;left:7px;right:7px;top:8px;height:4px;border-radius:2px;background:rgba(128,128,128,.3)}
.stacsel-fill{position:absolute;top:8px;height:4px;border-radius:2px;background:#3b82f6}
.stacsel-sl input{position:absolute;left:0;top:0;width:100%;height:20px;margin:0;padding:0;background:none;
  pointer-events:none;-webkit-appearance:none;appearance:none}
.stacsel-sl input::-webkit-slider-runnable-track{height:20px;background:none}
.stacsel-sl input::-moz-range-track{height:20px;background:none}
.stacsel-sl input::-webkit-slider-thumb{-webkit-appearance:none;pointer-events:auto;box-sizing:border-box;width:14px;height:14px;
  margin-top:3px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 0 1px #1d4ed8;cursor:pointer}
.stacsel-sl input::-moz-range-thumb{pointer-events:auto;box-sizing:border-box;width:14px;height:14px;
  border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 0 1px #1d4ed8;cursor:pointer}
.stacsel-tk{display:flex;justify-content:space-between;font-size:11px;opacity:.6}
.stacsel-pre{display:flex;gap:6px;margin-top:6px}
.stacsel-opts{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:8px}
.stacsel-opts .ccv{min-width:36px;opacity:.7}
.stacsel-sels{display:contents}
.stacsel-draw.sm{font-size:11px;padding:1px 8px}
.stacsel .leaflet-container .leaflet-control-attribution{font-size:9px;line-height:1.2;padding:0 4px;background:rgba(255,255,255,.6)}
.stacsel-err{color:#dc2626;font-size:12px;min-height:14px;margin-top:4px}
.stacsel-code{margin:6px 0 0;padding:6px 8px;background:rgba(128,128,128,.12);border-radius:6px;
  font-family:var(--jp-code-font-family,monospace);font-size:11.5px;white-space:pre-wrap}
"""

if anywidget is not None:

    class SearchSelector(anywidget.AnyWidget):
        """Draw a bbox or polygon, pick a date range and common filters; the values sync back to Python."""

        _esm = _ESM
        _css = _WIDGET_CSS
        bbox = traitlets.List(traitlets.Float(), default_value=[-180.0, -90.0, 180.0, 90.0]).tag(sync=True)
        polygon = traitlets.List(traitlets.List(traitlets.Float()), default_value=[]).tag(sync=True)  # [[lon, lat], ...]
        cloud_cover = traitlets.Int(100).tag(sync=True)  # max %, 100 = no filter
        has_cloud = traitlets.Bool(False).tag(sync=True)
        filters = traitlets.Dict({}).tag(sync=True)  # {property: chosen value}
        filter_options = traitlets.Dict({}).tag(sync=True)  # {property: [values]}
        start = traitlets.Unicode("").tag(sync=True)
        end = traitlets.Unicode("").tag(sync=True)
        extent = traitlets.List(traitlets.Float(), default_value=[-180.0, -90.0, 180.0, 90.0]).tag(sync=True)
        min_date = traitlets.Unicode("").tag(sync=True)
        max_date = traitlets.Unicode("").tag(sync=True)

else:
    SearchSelector = None  # type: ignore[assignment,misc]


# ----------------------------------------------------------------- public classes
class Items(pystac.ItemCollection):
    """``pystac.ItemCollection`` with a notebook summary (count, map, first rows)."""

    def __init__(self, *args, limit: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit = limit  # max_items used for the search, to flag truncation

    def __repr__(self) -> str:
        return f"<Items n={len(self.items)}>"

    def _repr_html_(self) -> str:
        items, n = self.items, len(self.items)
        spans = [_item_span(i) for i in items]
        los = [lo for lo, _ in spans if lo]
        his = [hi or lo for lo, hi in spans if lo]
        note = " <span class='m'>max_items reached: results may be truncated</span>" if n and n == self._limit else ""
        rng = f"<div class='m'>{_day(min(los))} to {_day(max(his))}</div>" if los else ""
        bboxes = [i.bbox for i in items[:500] if i.bbox]
        when = lambda i, lo, hi: _day(lo) if i.datetime else f"{_day(lo)} to {_day(hi)}"
        feats, rows = [], ""
        for k, (i, (lo, hi)) in enumerate(zip(items[:500], spans)):
            if not i.geometry:
                continue
            w, cc = when(i, lo, hi), i.properties.get("eo:cloud_cover")
            props = {k_: i.properties[k_] for k_ in _POP_PROPS if i.properties.get(k_) is not None}
            if cc is not None:
                props["cloud cover"] = f"{cc:.0f}%"
            href = i.get_self_href()
            tm = _stamp(i.datetime) if i.datetime else f"{_stamp(lo)} to {_stamp(hi or lo)}" if lo else ""
            f = {"i": k, "t": f"{i.id} \u00b7 {w}", "id": i.id, "when": w, "time": tm, "props": props}
            if href and _is_http(href):
                f["href"] = href
            if thumb := _thumb(i):
                f["thumb"] = thumb
            feats.append({"type": "Feature", "geometry": i.geometry, "properties": f})
            rows += (
                f"<tr data-i='{k}'><td>{html.escape(i.id)}</td><td class='m nw'>{w}</td>"
                f"<td class='m nw'>{'' if cc is None else f'{cc:.0f}% cloud'}</td></tr>"
            )
        mp = _map_html({"features": feats}, _svg_map(bboxes, view=_items_view(bboxes))) if feats or bboxes else ""
        lst = f"<div class='il'><table><tr><th>id</th><th>date</th><th>cloud</th></tr>{rows}</table></div>" if rows else ""
        return (
            f"{_CSS}<div class='sc'><h4>{n} items{note}</h4>{rng}"
            f"<div class='row2'><div class='mp'>{mp}</div>{lst}</div></div>"
        )


class CollectionView:
    """Wraps a ``pystac.Collection``: delegates attributes, adds viewer and search.

    ``collection`` may be the raw JSON dict from ``/collections``; it is then parsed
    into a pystac object only on first use, so listing a large API stays cheap.
    """

    def __init__(self, collection: pystac.Collection | dict, catalog: Any):
        if isinstance(collection, dict):
            self._raw, self._parsed = collection, None
            self._id, self._title = collection["id"], collection.get("title")
        else:
            self._raw, self._parsed = None, collection
            self._id, self._title = collection.id, collection.title
        self._catalog = catalog

    @property
    def _col(self) -> pystac.Collection:
        if self._parsed is None:
            cat = self._catalog
            self._parsed = CollectionClient.from_dict(self._raw, root=cat, modifier=cat.modifier)
            call_modifier(cat.modifier, self._parsed)
            self._raw = None
        return self._parsed

    def __getattr__(self, name: str):
        if name.startswith("__") or name in {"_raw", "_parsed", "_id", "_title", "_catalog"}:
            raise AttributeError(name)
        return getattr(self._col, name)

    def __dir__(self):
        return sorted(set(dir(type(self))) | set(dir(self._col)))

    def __repr__(self) -> str:
        return f"<Collection {self._id}>"

    # ---- viewer
    def _repr_html_(self) -> str:
        c, e = self._col, self._col.extent
        interval = (e.temporal.intervals or [[None, None]])[0]
        t0, t1 = interval[0], interval[1]
        y0, left, width = _bar_span(t0, t1)
        temporal = (
            f"<div class='bar'><div style='left:{left:.1f}%;width:{width:.1f}%'></div></div>"
            f"<div class='tk'><span>{y0}</span><span>{dt.datetime.now(_UTC).year}</span></div>"
            f"<div>{_day(t0) or 'open'} to {_day(t1) or 'open'}</div>"
        )
        bboxes = e.spatial.bboxes or []
        head = {
            "id": c.id,
            "license": c.license,
            "providers": ", ".join(p.name for p in c.providers or []),
        }
        mp = ""
        if bboxes:
            data = {"bboxes": [list(_bbox4(b)) for b in bboxes]}
            mp = f"<div class='mp'>{_map_html(data, _svg_map(bboxes))}</div>"
        parts = [
            f"<h4>{html.escape(c.title or c.id)}</h4>",
            _desc_html(c.description),
            f"<div class='row'>{mp}<div class='info'><b>Temporal extent</b>{temporal}{_table(head)}</div></div>",
        ]
        summaries = c.summaries.to_dict() if getattr(c, "summaries", None) else {}
        if summaries:
            parts.append(f"<details><summary>Summaries ({len(summaries)})</summary>{_table(summaries)}</details>")
        ia = c.extra_fields.get("item_assets") or {}
        if ia:
            ia = {k: v if isinstance(v, dict) else {"title": str(v)} for k, v in ia.items()}
            parts.append(_assets_html("Item assets", ia))
        if c.assets:
            ca = {k: {"href": a.href, "type": a.media_type} for k, a in c.assets.items()}
            parts.append(_assets_html("Collection assets", ca, href=True))
        return f"{_CSS}<div class='sc'>{''.join(parts)}</div>"

    # ---- selector + search
    def selector(self):
        """Return an interactive bbox/polygon/date/filter picker (show it, then pass it to ``search(sel)``)."""
        if SearchSelector is None:
            raise ImportError("selector() needs anywidget: pip install anywidget")
        col = self._col
        bbox, start, end = _extent_defaults(col)
        summ = col.summaries.to_dict()
        has_cloud = "eo:cloud_cover" in summ or "eo:bands" in summ or any("/eo/" in u for u in col.stac_extensions or [])
        opts = {
            k: sorted(summ[k]) for k in _FILTER_KEYS
            if isinstance(summ.get(k), list) and 2 <= len(summ[k]) <= 20 and all(isinstance(v, str) for v in summ[k])
        }
        return SearchSelector(bbox=bbox, extent=bbox, start=start, end=end, min_date=start, max_date=end,
                              has_cloud=has_cloud, filter_options=opts)

    def search(self, selector=None, *, bbox=None, intersects=None, datetime=None, ids=None, query=None,
               max_items=1000, **kwargs) -> Items:
        """Search this collection. ``selector`` (from ``selector()``) supplies the area (bbox or polygon),
        datetime and query (cloud cover, filters); explicit arguments win (``query`` per key).

        STAC APIs: server-side search (extra kwargs go to ``pystac_client``). Static catalogs:
        client-side bbox/intersects (bounding box only)/datetime/ids/query filtering only. ``intersects`` is a
        GeoJSON geometry dict. ``datetime`` takes 'a/b', 'a', '../b', datetime/date objects, or a (start, end)
        tuple. ``max_items=None`` means no limit.
        """
        if selector is not None:
            if bbox is None and intersects is None:  # APIs reject bbox + intersects together
                if selector.polygon:
                    ring = [list(p) for p in selector.polygon]
                    intersects = {"type": "Polygon", "coordinates": [ring + [ring[0]]]}
                else:
                    bbox = list(selector.bbox)
            if datetime is None and (selector.start or selector.end):
                datetime = f"{selector.start or '..'}/{selector.end or '..'}"
            sq = {k: {"eq": v} for k, v in selector.filters.items()}
            if selector.cloud_cover < 100:
                sq["eo:cloud_cover"] = {"lte": selector.cloud_cover}
            query = {**sq, **(query or {})}
        cat = self._catalog
        if isinstance(cat, Client) and cat.conforms_to(ConformanceClasses.ITEM_SEARCH):
            t0, t1 = _dt_range(datetime)
            search = cat.search(
                collections=[self._col.id], bbox=bbox, intersects=intersects, datetime=(t0, t1) if t0 or t1 else None,
                ids=ids, query=query or None, max_items=max_items, **kwargs,
            )
            found = list(search.items())
        else:
            found = _local_search(self._col, bbox, datetime, ids, max_items, intersects, query, **kwargs)
        return Items(found, clone_items=False, limit=max_items)


# --------------------------------------------------------------------------- collection cache
def _fetch_collections(catalog: Any) -> list[pystac.Collection | dict]:
    if isinstance(catalog, Client) and catalog._supports_collections():
        # raw JSON pages; CollectionView parses each one only when it is opened
        return [c for page in _collection_pages(catalog) for c in page["collections"]]
    return list(catalog.get_collections() if isinstance(catalog, Client) else catalog.get_all_collections())


def _cache_dir() -> Path:
    """``$STAC_VIEWER_CACHE``, else ``$XDG_CACHE_HOME/stac-viewer``, else ``~/.cache/stac-viewer``."""
    if os.environ.get("STAC_VIEWER_CACHE"):
        return Path(os.environ["STAC_VIEWER_CACHE"])
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "stac-viewer"


def _cached_collections(key: str, catalog: Any, refresh: bool = False) -> list[pystac.Collection | dict]:
    """Collections of ``catalog``, read from a JSON cache keyed by ``key`` if younger than ``CACHE_MAX_AGE``."""
    path = _cache_dir() / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE.total_seconds():
        try:
            entries = json.loads(path.read_text())["collections"]
            if isinstance(catalog, Client):  # keep API collections raw so they are parsed lazily
                return [d for _, d in entries]
            # self href passed separately so relative links (static catalogs) still resolve
            return [pystac.Collection.from_dict(d, href=href) for href, d in entries]
        except (OSError, ValueError, KeyError, TypeError, pystac.STACError):
            pass  # unreadable cache: fetch again
    cols = _fetch_collections(catalog)
    entries = [
        [None, c] if isinstance(c, dict) else [c.get_self_href(), c.to_dict(include_self_link=False, transform_hrefs=False)]
        for c in cols
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"source": key, "collections": entries}))
        tmp.replace(path)
    except OSError:
        pass  # caching is best effort
    return cols


class Collections:
    """Dot-notation access to the collections of a STAC catalog or API."""

    def __init__(self, catalog: Any, collections: list[pystac.Collection | dict] | None = None):
        self._catalog = catalog
        self._by_attr: dict[str, CollectionView] = {}
        self._by_id: dict[str, CollectionView] = {}
        cols = _fetch_collections(catalog) if collections is None else collections
        for c in cols:
            view = CollectionView(c, catalog)
            self._by_attr[_ident(view._id, set(self._by_attr))] = view
            self._by_id[view._id] = view

    def __getattr__(self, name: str) -> CollectionView:
        if name.startswith("__") or name in {"_catalog", "_by_attr", "_by_id"}:
            raise AttributeError(name)
        try:
            return self._by_attr[name]
        except KeyError:
            raise AttributeError(f"no collection '{name}'") from None

    def __getitem__(self, collection_id: str) -> CollectionView:
        return self._by_id[collection_id]

    def __iter__(self) -> Iterator[CollectionView]:
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def __dir__(self):
        return list(self._by_attr) + [m for m in dir(type(self)) if not m.startswith("__")]

    def __repr__(self) -> str:
        return "\n".join(f"{k}: {v._title or ''}" for k, v in self._by_attr.items())

    def _repr_html_(self) -> str:
        rows = "".join(
            f"<tr><td><b>{html.escape(k)}</b></td><td class='m'>{html.escape(v._title or '')}</td></tr>"
            for k, v in self._by_attr.items()
        )
        return f"{_CSS}<div class='sc'><table>{rows}</table></div>"


def browse(source: Any, *, refresh: bool = False, **client_kwargs) -> Collections:
    """Open ``source`` (API URL, catalog.json path/URL, or pystac/pystac-client object).

    ``client_kwargs`` go to ``pystac_client.Client.open`` (e.g. ``modifier=``, ``headers=``).
    For URL/path sources the collection list is cached on disk (see ``_cache_dir``) and
    refetched when older than ``CACHE_MAX_AGE`` (5 days) or when ``refresh=True``.
    """
    if not isinstance(source, str):
        return Collections(source)
    key = source = source.rstrip("/").removesuffix("/collections")
    if "://" not in key:
        key = os.path.abspath(key)
    if source.endswith(".json"):
        source = pystac.Catalog.from_file(source)
    else:
        source = Client.open(source, **client_kwargs)
    return Collections(source, _cached_collections(key, source, refresh))

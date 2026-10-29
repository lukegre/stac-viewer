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

Dependencies: pystac, pystac-client; anywidget (optional, only for ``selector()``); ``open()`` backends are
optional extras (odc-stac, xpystac, h5netcdf, geopandas; see ``openers``).
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

from . import openers as _openers
from .openers import open_assets

try:  # optional: interactive selector
    import anywidget
    import traitlets
except ImportError:  # pragma: no cover
    anywidget = None
    traitlets = None

__all__ = ["browse", "Collections", "CollectionView", "Items", "SearchSelector", "OpenSettings"]

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
.sc .pop>div{min-width:0;max-width:220px}.sc .pop div div{color:var(--sv-muted)}
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
                f'height="{max(y(s) - y(n), 2):.1f}" fill-opacity="{.35 if i == 0 else .2}" '
                f'style="fill:var(--sv-accent);stroke:var(--sv-accent)"><title>[{bw:.2f}, {s:.2f}, {be:.2f}, {n:.2f}]</title></rect>'
            )
    sx, sy = w / (east - west), h / (north - south)
    move = "" if view is None else f"translate({-(west + 180) * sx:.4f}, {-(90 - north) * sy:.4f}) "
    land = f'<path d="{_LAND}" transform="{move}scale({sx:.4f}, {sy:.4f})" fill="currentColor" fill-opacity=".25"/>'
    clip = "" if view is None else ";overflow:hidden"
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="color:var(--sv-muted){clip}">{land}'
        f'<g stroke="currentColor" stroke-opacity=".3" stroke-width="1">{grid}</g>{rects}</svg>'
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
    rows = "".join(f"<dt>{html.escape(str(k))}</dt><dd>{html.escape(str(v))}</dd>" for k, v in d.items())
    return f"<dl class='kv'>{rows}</dl>"


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
            f"<tr><td class='k'>{html.escape(str(k))}</td>"
            f"<td title='{html.escape(t, quote=True)}'>{html.escape(_media(t))}</td>"
            f"<td class='m'>{html.escape(str(a.get('href' if href else 'title') or ''))}</td></tr>"
        )
    kinds = ", ".join(dict.fromkeys(k for k in kinds if k))
    head = f"<tr><th>key</th><th>format</th><th>{'href' if href else 'title'}</th></tr>"
    return (
        f"<details><summary>{label} <span>{len(rows)}{' · ' + html.escape(kinds) if kinds else ''}</span></summary>"
        f"<table class='tb at'><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></details>"
    )


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
async function leaflet() {  // CSS link once per page; dynamic import avoids RequireJS clashes
  if (!document.getElementById("stacmap-css")) {
    const l = document.createElement("link");
    l.id = "stacmap-css"; l.rel = "stylesheet"; l.href = LEAF + "leaflet.css";
    await new Promise(r => { l.onload = l.onerror = r; document.head.append(l); });
  }
  if (!document.getElementById("stacmap-style")) {
    const st = document.createElement("style");
    st.id = "stacmap-style"; st.textContent = LEAF_CSS;
    document.head.append(st);
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
  const L = await leaflet();
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
      sel = i; row(i, "sel", true); paint();
      const r = rows[i], c = r && r.closest(".il");
      if (scroll && c) c.scrollTop += r.getBoundingClientRect().top - c.getBoundingClientRect().top - (c.clientHeight - r.offsetHeight) / 2;  // list only, not the page
    };
    map.on("popupclose", () => { if (sel != null) row(sel, "sel", false); sel = null; paint(); });
    L.geoJSON(data.features, { style: base, onEachFeature: (f, l) => {
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
        f'<div id="{uid}" class="map">{fallback_svg}</div>'
        f"<script>(async()=>{{const id={_json(uid)},data={_json(data)};{_LEAFLET_JS}{_MAP_JS}}})()</script>"
    )


_ICON = "<svg viewBox='0 0 16 16' fill='none' stroke='currentColor' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{}</svg>"
_SEARCH_SVG = _ICON.format("<circle cx='7' cy='7' r='4.5'/><path d='M10.5 10.5 14 14'/>")
_COPY_SVG = _ICON.format("<rect x='5.5' y='5.5' width='8' height='8' rx='1.5'/><path d='M10.5 5.5V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2'/>")
_CHECK_SVG = _ICON.format("<path d='M3 8.5 6.5 12 13 4.5'/>")


# ------------------------------------------------------------ interactive selector
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

  const L = await leaflet();
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
      <div><p class="lbl">Open call</p><div class="stacsel-cw"><pre class="stacsel-code"></pre>
        <button type="button" class="stacsel-cp" aria-label="Copy open call"></button></div>
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

    class OpenSettings(anywidget.AnyWidget):
        """Pick assets and opener settings; pass it to ``open(w)`` (explicit kwargs win)."""

        _esm = _OPEN_ESM
        _css = _WIDGET_CSS
        asset_options = traitlets.List(traitlets.Dict()).tag(sync=True)  # [{key, label, opener, data}]
        assets = traitlets.List(traitlets.Unicode()).tag(sync=True)  # selected keys
        opener = traitlets.Unicode("").tag(sync=True)
        params = traitlets.Dict({}).tag(sync=True)  # {name: {kind, choices?, default, inferred}}
        values = traitlets.Dict({}).tag(sync=True)  # {name: current value}
        code = traitlets.Unicode("").tag(sync=True)
        error = traitlets.Unicode("").tag(sync=True)

        def __init__(self, owners: list):
            self._owners = list(owners)
            super().__init__(asset_options=_openers.options(self._owners))
            try:
                self.assets = _openers.resolve(self._owners)[1]
            except (ValueError, KeyError) as e:
                self.error = str(e)

        @traitlets.observe("assets")
        def _assets_changed(self, _):
            if not self.assets:
                self.opener, self.params, self.values, self.error = "", {}, {}, ""
                return self._sync_code()
            try:
                name, keys = _openers.resolve(self._owners, self.assets)
                params = _openers.opener_params(name, self._owners, keys)
            except (ValueError, KeyError) as e:
                self.error = str(e)
                return
            old = self.params if name == self.opener else {}  # same opener: keep values the user edited
            vals = {k: self.values.get(k) if k in old and self.values.get(k) != old[k]["default"] else p["default"]
                    for k, p in params.items()}
            self.error, self.opener, self.params, self.values = "", name, params, vals
            self._sync_code()

        @traitlets.observe("values")
        def _sync_code(self, _=None):
            try:
                kw = self.kwargs()
            except ValueError as e:
                self.error = str(e)
                return
            self.error = ""
            call = "".join(f"  {k}={v!r},\n" for k, v in {"assets": list(self.assets), **kw}.items())
            self.code = f"open(\n{call})" if self.assets else ""

        def set(self, assets=None, **values) -> "OpenSettings":
            """Set the selection and settings from code, e.g. ``w.set(assets=["B04"], crs="EPSG:4326")``.

            ``assets`` is applied first (switching opener resets the settings to its defaults); returns ``self``.
            """
            if assets is not None:
                self.assets = [assets] if isinstance(assets, str) else list(assets)
                if self.error:
                    raise ValueError(self.error)
            if bad := [k for k in values if k not in self.params]:
                raise KeyError(f"unknown settings {bad} for opener {self.opener!r}. Available: {', '.join(self.params) or 'none'}")
            self.values = {**self.values, **values}
            if self.error:
                raise ValueError(self.error)
            return self

        def kwargs(self) -> dict:
            """Opener settings to pass to ``open``: set, and either inferred or changed from the default."""
            out = {}
            for k, p in self.params.items():
                v = self.values.get(k)
                if v is None or v == "":
                    continue
                try:
                    v = {"float": float, "int": int}.get(p["kind"], lambda x: x)(v)
                except ValueError:
                    raise ValueError(f"{k}: expected {p['kind']}, got {v!r}") from None
                if p["inferred"] or v != p["default"]:
                    out[k] = v
            return out

else:
    SearchSelector = OpenSettings = None  # type: ignore[assignment,misc]


# ----------------------------------------------------------------- public classes
def _open(owners: list, assets, opener, kwargs: dict):
    if OpenSettings is not None and isinstance(assets, OpenSettings):
        assets, opener, kwargs = assets.assets, opener or assets.opener or None, {**assets.kwargs(), **kwargs}
    return open_assets(owners, assets, opener, **kwargs)


class Items(pystac.ItemCollection):
    """``pystac.ItemCollection`` with a notebook summary (count, map, first rows)."""

    def __init__(self, *args, limit: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit = limit  # max_items used for the search, to flag truncation

    def __repr__(self) -> str:
        return f"<Items n={len(self.items)}>"

    def open(self, assets=None, *, opener: str | None = None, **kwargs):
        """Open assets as an xarray Dataset or (Geo)DataFrame; default is all assets with role 'data'.

        ``opener`` forces a registered opener by name; ``kwargs`` go to the backend. ``assets`` may also be an
        ``open_settings()`` widget: its selection and settings are used, explicit arguments win.
        """
        return _open(list(self.items), assets, opener, kwargs)

    def open_settings(self):
        """Return an interactive asset/opener-settings picker (show it, then pass it to ``open(w)``)."""
        if OpenSettings is None:
            raise ImportError("open_settings() needs anywidget: pip install anywidget")
        return OpenSettings(list(self.items))

    def _repr_html_(self) -> str:
        items, n = self.items, len(self.items)
        spans = [_item_span(i) for i in items]
        los = [lo for lo, _ in spans if lo]
        his = [hi or lo for lo, hi in spans if lo]
        note = "max_items reached: results may be truncated" if n and n == self._limit else ""
        rng = f"{_day(min(los))} &rarr; {_day(max(his))}" if los else ""
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
            bar = "" if cc is None else f"<span class='cb'><i style='width:{min(max(cc, 0), 100):.0f}%'></i></span>{cc:.0f}%"
            rows += (
                f"<tr data-i='{k}'><td class='id' title='{html.escape(i.id)}'>{html.escape(i.id)}</td><td class='m nw'>{w}</td>"
                f"<td class='m nw'{'' if cc is None else f' title=\'{cc:.0f}% cloud\''}>{bar}</td></tr>"
            )
        mp = _map_html({"features": feats}, _svg_map(bboxes, view=_items_view(bboxes))) if feats or bboxes else ""
        lst = (f"<div class='il'><table class='tb'><thead><tr><th>id</th><th>date</th><th>cloud</th></tr></thead>"
               f"<tbody>{rows}</tbody></table></div>" if rows else "")
        meta = " &middot; ".join(x for x in (note, rng) if x)
        return (
            f"{_CSS}<div class='sc'><div class='hd'><span class='ty'>Items</span><span class='tt'>{n} items</span>"
            f"<span class='mt'>{meta}</span></div>"
            f"<div class='grid eq'><div class='mp'>{mp}</div>{lst}</div></div>"
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

    def open(self, assets=None, *, opener: str | None = None, **kwargs):
        """Open collection-level assets (default: those with role 'data'); see ``Items.open``."""
        return _open([self._col], assets, opener, kwargs)

    def open_settings(self):
        """Return an interactive asset/opener-settings picker for collection assets; see ``Items.open_settings``."""
        if OpenSettings is None:
            raise ImportError("open_settings() needs anywidget: pip install anywidget")
        return OpenSettings([self._col])

    # ---- viewer
    def _repr_html_(self) -> str:
        c, e = self._col, self._col.extent
        interval = (e.temporal.intervals or [[None, None]])[0]
        t0, t1 = interval[0], interval[1]
        y0, left, width = _bar_span(t0, t1)
        temporal = (
            f"<p class='lbl'>Temporal extent</p><div class='bar'><div style='left:{left:.1f}%;width:{width:.1f}%'></div></div>"
            f"<div class='tk'><span>{y0}</span><span>{dt.datetime.now(_UTC).year}</span></div>"
            f"<div class='dt'>{_day(t0) or 'open'} &rarr; {_day(t1) or 'open'}</div>"
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
        meta = html.escape(" \u00b7 ".join(str(x) for x in (c.id, c.license) if x))
        parts = [
            f"<div class='hd'><span class='ty'>STAC Collection</span><span class='tt'>{html.escape(c.title or c.id)}</span>"
            f"<span class='mt'>{meta}</span></div>",
            _desc_html(c.description),
            f"<div class='grid{'' if mp else ' one'}'>{mp}<div class='info'>{temporal}<p class='lbl'>Details</p>{_table(head)}</div></div>",
        ]
        summaries = c.summaries.to_dict() if getattr(c, "summaries", None) else {}
        if summaries:
            parts.append(f"<details><summary>Summaries <span>{len(summaries)}</span></summary>{_table(summaries)}</details>")
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


def _kw_desc(view: CollectionView) -> tuple[list, str]:
    """Keywords and description, read from the raw dict when unparsed (no parsing)."""
    if view._raw is not None:
        return view._raw.get("keywords") or [], view._raw.get("description") or ""
    return view._parsed.keywords or [], view._parsed.description or ""


def _search_text(attr: str, view: CollectionView) -> str:
    """Text matched by the overview search box."""
    kw, desc = _kw_desc(view)
    return " ".join([attr, view._id, view._title or "", *map(str, kw), desc])


def _tooltip(view: CollectionView, n: int = 400) -> str:
    """Hover text for an overview row: title, then the description cut to ~n chars."""
    _, desc = _kw_desc(view)
    desc = " ".join(desc.split())
    if len(desc) > n:
        desc = desc[:n].rsplit(" ", 1)[0] + "\u2026"
    return f"{view._title or view._id}\n\n{desc}" if desc else view._title or view._id


_IMG_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


def _col_thumb(view: CollectionView) -> str | None:
    """Collection preview image URL (http(s) PNG/JPEG/...): thumbnail-role asset first, else any image asset."""
    if view._raw is not None:
        assets = list((view._raw.get("assets") or {}).values())
    else:
        assets = [a.to_dict() for a in view._parsed.assets.values()]
    imgs = [a for a in assets if isinstance(a, dict) and _is_http(a.get("href") or "") and (
        a.get("type") in _IMG_TYPES or a["href"].lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")))]
    a = next((a for a in imgs if "thumbnail" in (a.get("roles") or [])), imgs[0] if imgs else None)
    return a and a["href"]


# rows whose data-q contains every whitespace-separated term stay visible
_FILTER_JS = """
const el = document.getElementById(id), box = el.querySelector(".q"), inp = box.querySelector("input");
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
        cat, n = self._catalog, len(self._by_attr)
        title = getattr(cat, "title", None) or getattr(cat, "id", None) or "STAC catalog"
        href = cat.get_self_href() if hasattr(cat, "get_self_href") else None
        api = isinstance(cat, Client) and cat.conforms_to(ConformanceClasses.ITEM_SEARCH)
        head = {"id": getattr(cat, "id", ""), "type": "STAC API (item search)" if api else "static catalog"}
        if href:
            head["url"] = href
        img = lambda v: f" data-img='{html.escape(u)}'" if (u := _col_thumb(v)) else ""
        rows = "".join(
            f"<tr data-q='{html.escape(_search_text(k, v).lower())}' title='{html.escape(_tooltip(v))}'{img(v)}>"
            f"<td class='ti'>{html.escape(v._title or '')}</td><td class='id'>{html.escape(k)}"
            f"<button type='button' class='cp' data-id='{html.escape(k)}' aria-label='Copy id' hidden>{_COPY_SVG}</button></td></tr>"
            for k, v in self._by_attr.items()
        )
        uid = f"stacq-{uuid.uuid4().hex}"
        return (
            f"{_CSS}<div class='sc' id='{uid}'><div class='hd'><span class='ty'>{'STAC API' if api else 'STAC catalog'}</span>"
            f"<span class='tt'>{html.escape(title)}</span><span class='mt'>{n} collections</span></div>"
            f"{_desc_html(getattr(cat, 'description', None))}<div style='margin-top:10px'>{_table(head)}</div>"
            f"<div class='q' hidden>{_SEARCH_SVG}<input type='search' placeholder='Filter by title, id, keyword or description'>"
            f"<span class='m'></span></div><div class='il ovl'><table class='tb ov'><tbody>{rows}</tbody></table></div></div>"
            f"<script>(()=>{{const id={_json(uid)},CHECK={_json(_CHECK_SVG)};{_FILTER_JS}}})()</script>"
        )


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

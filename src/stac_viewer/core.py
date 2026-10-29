"""stac_viewer: browse a STAC catalog with dot notation and rich notebook viewers.

Usage (Jupyter / JupyterLab / VS Code / Colab)::

    from stac_viewer import browse

    cat = browse("https://earth-search.aws.element84.com/v1")  # API URL, catalog.json path/URL or pystac object
    cat                                   # overview table of collections
    cat.sentinel_2_l2a                    # HTML viewer: map, temporal bar, metadata
    sel = cat.sentinel_2_l2a.selector()   # interactive bbox + date picker; show it with `sel`
    items = cat.sentinel_2_l2a.search(sel)  # search using the selection -> Items
    items = cat.sentinel_2_l2a.search(bbox=[8.4, 47.3, 8.7, 47.5], datetime="2026-06-01/2026-06-30", max_items=50)
    items                                 # HTML: count, map of item footprints, first rows

    browse(url, modifier=planetary_computer.sign_inplace)  # extra kwargs go to pystac_client.Client.open
    browse(url, refresh=True)             # ignore the collection cache (refreshed anyway after 5 days)

Dependencies: pystac, pystac-client, anywidget; ``open()`` backends are
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

import anywidget
import pystac
import traitlets
from pystac_client import Client
from pystac_client._utils import call_modifier
from pystac_client.collection_client import CollectionClient
from pystac_client.conformance import ConformanceClasses
from pystac_client.exceptions import APIError

from . import openers as _openers
from ._frontend import (_CHECK_SVG, _COPY_SVG, _CSS, _ESM, _FILTER_JS, _LEAFLET_JS, _MAP_JS, _OPEN_ESM,
                        _SEARCH_SVG, _WIDGET_CSS)
from .openers import open_assets


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


def _day_time(d: dt.datetime | None) -> str:
    """'YYYY-MM-DD HH:MM' in UTC; just the day when the time is midnight (date-only items)."""
    if not d:
        return ""
    d = d.replace(tzinfo=_UTC) if d.tzinfo is None else d.astimezone(_UTC)
    return _day(d) if d.time() == dt.time() else d.strftime("%Y-%m-%d %H:%M")


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
    "text/csv": "CSV",
    "text/tab-separated-values": "TSV",
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
_POP_PROPS = ("platform", "constellation", "gsd", "proj:epsg", "sat:orbit_state")  # listed first
_POP_SKIP = {"datetime", "start_datetime", "end_datetime", "created", "updated", "title", "description", "eo:cloud_cover"}


def _pop_props(p: dict, n: int = 12) -> dict:
    """Popup properties: well-known keys first, then any other scalar ones; at most n, long strings cut."""
    keys = [k for k in _POP_PROPS if k in p] + [k for k in p if k not in _POP_PROPS and k not in _POP_SKIP]
    out = {}
    for k in keys:
        v = p[k]
        if v is None or not isinstance(v, (str, int, float, bool)):
            continue
        out[k] = v if not isinstance(v, str) or len(v) <= 80 else v[:79] + "…"
        if len(out) == n:
            break
    return out


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


# ------------------------------------------------------------ interactive selector
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
        return OpenSettings(list(self.items))

    def _repr_html_(self) -> str:
        items, n = self.items, len(self.items)
        spans = [_item_span(i) for i in items]
        los = [lo for lo, _ in spans if lo]
        his = [hi or lo for lo, hi in spans if lo]
        note = "max_items reached: results may be truncated" if n and n == self._limit else ""
        rng = f"{_day(min(los))} &rarr; {_day(max(his))}" if los else ""
        bboxes = [i.bbox for i in items[:500] if i.bbox]
        when = lambda i, lo, hi: _day_time(lo) if i.datetime else f"{_day_time(lo)} to {_day_time(hi)}"
        feats, rows = [], ""
        for k, (i, (lo, hi)) in enumerate(zip(items[:500], spans)):
            if not i.geometry:
                continue
            w, cc = when(i, lo, hi), i.properties.get("eo:cloud_cover")
            props = _pop_props(i.properties)
            if cc is not None:
                props["cloud cover"] = f"{cc:.0f}%"
            href = i.get_self_href()
            tm = _stamp(i.datetime) if i.datetime else f"{_stamp(lo)} to {_stamp(hi or lo)}" if lo else ""
            title = str(i.properties.get("title") or "")
            f = {"i": k, "t": " \u00b7 ".join(x for x in (i.id, title, w) if x), "id": i.id, "title": title,
                 "when": w, "time": tm, "props": props}
            if href and _is_http(href):
                f["href"] = href
            if thumb := _thumb(i):
                f["thumb"] = thumb
            feats.append({"type": "Feature", "geometry": i.geometry, "properties": f})
            bar = "" if cc is None else f"<span class='cb'><i style='width:{min(max(cc, 0), 100):.0f}%'></i></span>{cc:.0f}%"
            rows += (
                f"<tr data-i='{k}'><td class='id' title='{html.escape(f['t'])}'>{html.escape(i.id)}"
                f"{f' <span class=\'m\'>{html.escape(title)}</span>' if title else ''}</td><td class='m nw'>{w}</td>"
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

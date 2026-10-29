"""stac_viewer: browse a STAC catalog with dot notation and rich notebook viewers.

Usage (Jupyter / JupyterLab / VS Code / Colab)::

    from stac_viewer import browse

    cat = browse("https://earth-search.aws.element84.com/v1")  # API URL, catalog.json path/URL or pystac object
    cat                                   # overview table of collections
    cat.sentinel_2_l2a                    # HTML viewer: map, temporal bar, metadata
    cat.sentinel_2_l2a.selector()         # interactive bbox + date picker (needs anywidget)
    items = cat.sentinel_2_l2a.search()   # search using the current selection -> Items
    items = cat.sentinel_2_l2a.search(bbox=[8.4, 47.3, 8.7, 47.5], datetime="2026-06-01/2026-06-30", max_items=50)
    items                                 # HTML: count, map of item footprints, first rows
    cat.sentinel_2_l2a.clear_selection()  # search() stops using the selector

    browse(url, modifier=planetary_computer.sign_inplace)  # extra kwargs go to pystac_client.Client.open

Dependencies: pystac, pystac-client; anywidget (optional, only for ``selector()``).
"""
from __future__ import annotations

import datetime as dt
import html
import keyword
import math
import re
from importlib.resources import files
from typing import Any, Iterator

import pystac
from pystac_client import Client
from pystac_client.conformance import ConformanceClasses

try:  # optional: interactive selector
    import anywidget
    import traitlets
except ImportError:  # pragma: no cover
    anywidget = None
    traitlets = None

__all__ = ["browse", "Collections", "CollectionView", "Items", "SearchSelector"]

_LAND = files("stac_viewer").joinpath("land.txt").read_text()  # SVG path, 0..360 x 0..180 (x=lon+180, y=90-lat)
_UTC = dt.timezone.utc


# --------------------------------------------------------------------------- helpers
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

def _local_search(col: pystac.Collection, bbox=None, datetime=None, ids=None, max_items=None, **kwargs):
    """Minimal bbox/datetime/ids filter for static catalogs (no STAC API available)."""
    if kwargs:
        raise TypeError(
            f"unsupported kwargs {', '.join(kwargs)}: static catalogs only support bbox, datetime, ids, max_items"
        )
    t0, t1 = _dt_range(datetime)
    out = []
    for item in col.get_items(recursive=True):
        if ids and item.id not in ids:
            continue
        if bbox and item.bbox:
            w, s, e, n = _bbox4(item.bbox)
            qw, qs, qe, qn = _bbox4(bbox)
            if e < qw or w > qe or n < qs or s > qn:
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
 border-radius:8px;padding:10px;max-width:640px;color:inherit}
.sc h4{margin:0 0 2px;font-size:15px}.sc .m{opacity:.65}
.sc details{overflow-x:auto;margin-top:6px;border-top:1px solid rgba(128,128,128,.25);padding-top:6px}
.sc summary{cursor:pointer;font-weight:600}
.sc table{border-collapse:collapse}.sc td{padding:1px 10px 1px 0;vertical-align:top}
.sc .row{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;margin-top:8px}
.sc .bar{position:relative;height:8px;background:rgba(128,128,128,.25);border-radius:4px;margin:6px 0 2px}
.sc .bar>div{position:absolute;top:0;bottom:0;background:#3b82f6;border-radius:4px}
.sc .nw{white-space:nowrap}
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
        f'<svg width="{w}" height="{h}" style="border:1px solid rgba(128,128,128,.4){clip}">{land}'
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


# ------------------------------------------------------------ interactive selector
_ESM = """
const W = 240, H = 120;
const NS = "http://www.w3.org/2000/svg";
const lon = x => x / W * 360 - 180, lat = y => 90 - y / H * 180;
const px = v => (v + 180) / 360 * W, py = v => (90 - v) / 180 * H;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function render({ model, el }) {
  el.classList.add("stacsel");
  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" width="100%" class="stacsel-map"></svg>
    <div class="stacsel-hint">Drag on the map to set the bbox</div>
    <div class="stacsel-row">
      <label>Start<input type="date" class="d0"></label>
      <label>End<input type="date" class="d1"></label>
    </div>
    <div class="stacsel-err"></div>
    <pre class="stacsel-code"></pre>`;
  const svg = el.querySelector("svg"), err = el.querySelector(".stacsel-err");
  const d0 = el.querySelector(".d0"), d1 = el.querySelector(".d1");
  const code = el.querySelector(".stacsel-code");
  const mk = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };
  const land = mk("path", { d: model.get("land"), transform: `scale(${W / 360},${H / 180})`, fill: "rgba(128,128,128,.28)" });
  const grid = mk("g", { stroke: "rgba(128,128,128,.35)", "stroke-width": 0.5 });
  for (let i = 0; i <= 6; i++) {
    grid.append(mk("line", { x1: i * 40, y1: 0, x2: i * 40, y2: H }));
    grid.append(mk("line", { x1: 0, y1: i * 20, x2: W, y2: i * 20 }));
  }
  const ext = mk("rect", { fill: "none", stroke: "rgba(128,128,128,.8)", "stroke-dasharray": "3 2" });
  const sel = mk("rect", { fill: "#3b82f6", "fill-opacity": 0.3, stroke: "#1d4ed8" });
  svg.append(land, grid, ext, sel);

  function place(r, b) {
    r.setAttribute("x", px(b[0])); r.setAttribute("y", py(b[3]));
    r.setAttribute("width", Math.max(px(b[2]) - px(b[0]), 2));
    r.setAttribute("height", Math.max(py(b[1]) - py(b[3]), 2));
  }
  function draw() {
    const b = model.get("bbox");
    place(ext, model.get("extent")); place(sel, b);
    d0.value = model.get("start"); d1.value = model.get("end");
    d0.min = d1.min = model.get("min_date") || "";
    const bad = d0.value && d1.value && d0.value > d1.value;
    err.textContent = bad ? "Start must be before end" : "";
    code.textContent = `search(\\n  bbox=[${b.map(v => v.toFixed(2)).join(", ")}],\\n  datetime="${d0.value}/${d1.value}",\\n)`;
  }

  let anchor = null;
  const pt = e => {
    const r = svg.getBoundingClientRect();
    return [clamp((e.clientX - r.left) / r.width * W, 0, W), clamp((e.clientY - r.top) / r.height * H, 0, H)];
  };
  svg.addEventListener("pointerdown", e => { svg.setPointerCapture(e.pointerId); anchor = pt(e); });
  svg.addEventListener("pointermove", e => {
    if (!anchor) return;
    const p = pt(e);
    model.set("bbox", [lon(Math.min(anchor[0], p[0])), lat(Math.max(anchor[1], p[1])),
                       lon(Math.max(anchor[0], p[0])), lat(Math.min(anchor[1], p[1]))]);
    draw();
  });
  svg.addEventListener("pointerup", () => { anchor = null; model.save_changes(); });
  const setDate = (k, v) => { model.set(k, v); model.save_changes(); draw(); };
  d0.addEventListener("change", () => setDate("start", d0.value));
  d1.addEventListener("change", () => setDate("end", d1.value));
  model.on("change:bbox", draw); model.on("change:start", draw); model.on("change:end", draw);
  draw();
}
export default { render };
"""

_WIDGET_CSS = """
.stacsel{font:13px/1.4 var(--jp-ui-font-family,system-ui,sans-serif);border:1px solid rgba(128,128,128,.4);
  border-radius:8px;padding:10px;max-width:420px;color:inherit}
.stacsel-map{display:block;border:1px solid rgba(128,128,128,.4);cursor:crosshair;touch-action:none}
.stacsel-hint{font-size:11px;opacity:.6;margin-top:3px}
.stacsel-row{display:flex;gap:8px;margin-top:8px}
.stacsel-row label{flex:1;display:flex;flex-direction:column;font-size:12px;opacity:.85;gap:2px}
.stacsel-err{color:#dc2626;font-size:12px;min-height:14px;margin-top:4px}
.stacsel-code{margin:6px 0 0;padding:6px 8px;background:rgba(128,128,128,.12);border-radius:6px;
  font-family:var(--jp-code-font-family,monospace);font-size:11.5px;white-space:pre-wrap}
"""

if anywidget is not None:

    class SearchSelector(anywidget.AnyWidget):
        """Draw a bbox and pick a date range; the values sync back to Python."""

        _esm = _ESM
        _css = _WIDGET_CSS
        bbox = traitlets.List(traitlets.Float(), default_value=[-180.0, -90.0, 180.0, 90.0]).tag(sync=True)
        start = traitlets.Unicode("").tag(sync=True)
        end = traitlets.Unicode("").tag(sync=True)
        extent = traitlets.List(traitlets.Float(), default_value=[-180.0, -90.0, 180.0, 90.0]).tag(sync=True)
        min_date = traitlets.Unicode("").tag(sync=True)
        land = traitlets.Unicode("").tag(sync=True)

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
        rows = ""
        for i, (lo, hi) in zip(items[:20], spans):
            when = _day(lo) if i.datetime else f"{_day(lo)} to {_day(hi)}"
            cc = i.properties.get("eo:cloud_cover")
            rows += (
                f"<tr><td>{html.escape(i.id)}</td><td class='m nw'>{when}</td>"
                f"<td class='m nw'>{'' if cc is None else f'{cc:.0f}% cloud'}</td></tr>"
            )
        return (
            f"{_CSS}<div class='sc'><h4>{n} items{note}</h4>{rng}"
            f"<div class='row'><div>{_svg_map(bboxes, view=_items_view(bboxes)) if bboxes else ''}</div></div>"
            f"<details><summary>First {min(n, 20)} items</summary><table>{rows}</table></details></div>"
        )


class CollectionView:
    """Wraps a ``pystac.Collection``: delegates attributes, adds viewer and search."""

    def __init__(self, collection: pystac.Collection, catalog: Any):
        self._col = collection
        self._catalog = catalog
        self._selector = None

    def __getattr__(self, name: str):
        if name.startswith("__") or name in {"_col", "_catalog", "_selector"}:
            raise AttributeError(name)
        return getattr(self._col, name)

    def __dir__(self):
        return sorted(set(dir(type(self))) | set(dir(self._col)))

    def __repr__(self) -> str:
        return f"<Collection {self._col.id}>"

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
        parts = [
            f"<h4>{html.escape(c.title or c.id)}</h4>",
            f"<div class='m'>{html.escape((c.description or '')[:300])}</div>",
            f"<div class='row'><div>{_svg_map(bboxes) if bboxes else ''}</div>"
            f"<div style='min-width:220px;flex:1'><b>Temporal extent</b>{temporal}{_table(head)}</div></div>",
        ]
        summaries = c.summaries.to_dict() if getattr(c, "summaries", None) else {}
        if summaries:
            parts.append(f"<details><summary>Summaries ({len(summaries)})</summary>{_table(summaries)}</details>")
        ia = c.extra_fields.get("item_assets") or {}
        if ia:
            rows = {k: (v.get("title") or v.get("type") or "") if isinstance(v, dict) else str(v) for k, v in ia.items()}
            parts.append(f"<details><summary>Item assets ({len(ia)})</summary>{_table(rows)}</details>")
        if c.assets:
            rows = {k: a.href for k, a in c.assets.items()}
            parts.append(f"<details><summary>Collection assets ({len(rows)})</summary>{_table(rows)}</details>")
        return f"{_CSS}<div class='sc'>{''.join(parts)}</div>"

    # ---- selector + search
    def selector(self):
        """Show an interactive bbox/date picker; ``search()`` then uses its values."""
        if SearchSelector is None:
            raise ImportError("selector() needs anywidget: pip install anywidget")
        bbox, start, end = _extent_defaults(self._col)
        self._selector = SearchSelector(bbox=bbox, extent=bbox, start=start, end=end, min_date=start, land=_LAND)
        return self._selector

    def clear_selection(self) -> None:
        """Drop the selector so ``search()`` stops using it."""
        self._selector = None

    def search(self, *, bbox=None, datetime=None, ids=None, max_items=1000, **kwargs) -> Items:
        """Search this collection. Explicit arguments win over the selector's values.

        STAC APIs: server-side search (extra kwargs go to ``pystac_client``). Static catalogs:
        client-side bbox/datetime/ids filtering only. ``datetime`` takes 'a/b', 'a', '../b',
        datetime/date objects, or a (start, end) tuple. ``max_items=None`` means no limit.
        """
        sel = self._selector
        if sel is not None:
            if bbox is None:
                bbox = list(sel.bbox)
            if datetime is None and (sel.start or sel.end):
                datetime = f"{sel.start or '..'}/{sel.end or '..'}"
        cat = self._catalog
        if isinstance(cat, Client) and cat.conforms_to(ConformanceClasses.ITEM_SEARCH):
            t0, t1 = _dt_range(datetime)
            search = cat.search(
                collections=[self._col.id], bbox=bbox, datetime=(t0, t1) if t0 or t1 else None,
                ids=ids, max_items=max_items, **kwargs,
            )
            found = list(search.items())
        else:
            found = _local_search(self._col, bbox, datetime, ids, max_items, **kwargs)
        return Items(found, clone_items=False, limit=max_items)


class Collections:
    """Dot-notation access to the collections of a STAC catalog or API."""

    def __init__(self, catalog: Any):
        self._catalog = catalog
        self._by_attr: dict[str, CollectionView] = {}
        self._by_id: dict[str, CollectionView] = {}
        cols = catalog.get_collections() if isinstance(catalog, Client) else catalog.get_all_collections()
        for c in cols:
            view = CollectionView(c, catalog)
            self._by_attr[_ident(c.id, set(self._by_attr))] = view
            self._by_id[c.id] = view

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
        return "\n".join(f"{k}: {v._col.title or ''}" for k, v in self._by_attr.items())

    def _repr_html_(self) -> str:
        rows = "".join(
            f"<tr><td><b>{html.escape(k)}</b></td><td class='m'>{html.escape(v._col.title or '')}</td></tr>"
            for k, v in self._by_attr.items()
        )
        return f"{_CSS}<div class='sc'><table>{rows}</table></div>"


def browse(source: Any, **client_kwargs) -> Collections:
    """Open ``source`` (API URL, catalog.json path/URL, or pystac/pystac-client object).

    ``client_kwargs`` go to ``pystac_client.Client.open`` (e.g. ``modifier=``, ``headers=``).
    """
    if isinstance(source, str):
        source = source.rstrip("/").removesuffix("/collections")
        if source.endswith(".json"):
            source = pystac.Catalog.from_file(source)
        else:
            source = Client.open(source, **client_kwargs)
    return Collections(source)

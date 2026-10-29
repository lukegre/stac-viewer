"""Open STAC assets as xarray / pandas objects through a small plugin registry.

An opener is ``f(items, assets, **kwargs)``: ``items`` is a list of objects with an
``.assets`` dict (``pystac.Item``, or a single ``pystac.Collection`` for collection
assets), ``assets`` the selected asset keys. It returns an ``xr.Dataset``,
``xr.DataArray`` (wrapped into a Dataset) or ``pd.DataFrame`` (incl. GeoDataFrame).

Register with :func:`register_opener`. Third-party packages can ship openers by
declaring an entry point in the ``stac_viewer.openers`` group; the entry point is
imported (once, on first open) and the module calls ``register_opener`` at import
time. Later registrations win over earlier ones, so plugins and user code override
the built-ins.
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import entry_points
from collections import Counter
from typing import Callable, NamedTuple
from urllib.parse import urlparse

import pystac

_REGISTRY: dict[str, dict] = {}
_loaded = False


def _norm(t: str) -> str:
    return t.lower().replace(" ", "")


class Param(NamedTuple):
    """Opener setting: ``kind`` is ``str``/``float``/``int``, a list of choices, or ``f(items, assets) -> choices``
    (e.g. read from the file); ``default`` is a value, None, or ``f(items, assets) -> value`` inferred from the selection."""

    kind: object
    default: object = None


def register_opener(name: str, media_types=(), extensions=(), params: dict[str, Param] | None = None):
    """Decorator registering ``f(items, assets, **kwargs)`` as opener ``name``; ``params`` declares its settings."""

    def deco(fn: Callable):
        _REGISTRY.pop(name, None)  # re-registering moves it to the end (highest priority)
        mt = [_norm(t) for t in media_types]
        _REGISTRY[name] = dict(
            fn=fn, exact=set(mt), base={t.split(";")[0] for t in mt}, ext={e.lower() for e in extensions}, params=params or {}
        )
        return fn

    return deco


def _load_plugins() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        for ep in entry_points(group="stac_viewer.openers"):
            ep.load()


def _ext(href: str) -> str:
    return os.path.splitext(urlparse(href).path.rstrip("/").lower())[1]


def _match(asset: pystac.Asset) -> str | None:
    """Opener name for an asset: exact media type, then base type, then extension."""
    t = _norm(asset.media_type or "")
    for field, val in [("exact", t), ("base", t.split(";")[0])]:
        if val:
            for name, o in reversed(_REGISTRY.items()):
                if val in o[field]:
                    return name
    path = urlparse(asset.href).path.rstrip("/").lower()
    for name, o in reversed(_REGISTRY.items()):  # suffix match, so compound extensions like .gdb.zip work
        if any(path.endswith(e) for e in o["ext"]):
            return name
    return None


def _fmt(key: str, asset: pystac.Asset) -> str:
    from .core import _media

    return f"{key} ({_media(asset.media_type) or _ext(asset.href) or 'unknown'})"


def _coerce(out, name: str):
    xr, pd = sys.modules.get("xarray"), sys.modules.get("pandas")
    if xr and isinstance(out, xr.DataArray):
        return out.to_dataset(name=out.name if out.name is not None else "data")
    if (xr and isinstance(out, xr.Dataset)) or (pd and isinstance(out, pd.DataFrame)):
        return out
    raise TypeError(f"opener {name!r} returned {type(out).__name__}; expected xarray Dataset/DataArray or pandas DataFrame")


def resolve(owners: list, assets=None, opener: str | None = None) -> tuple[str, list[str]]:
    """Opener name and asset keys that :func:`open_assets` would use."""
    _load_plugins()
    avail = _avail(owners)
    listing = ", ".join(_fmt(k, a) for k, a in avail.items()) or "none"
    if assets is None:
        keys = [k for k, a in avail.items() if "data" in (a.roles or []) and (opener or _match(a))]
        if not keys and len(avail) == 1 and (opener or _match(*avail.values())):  # sole asset, whatever its roles
            keys = list(avail)
        if not keys:  # data assets without an opener (e.g. JPEG2000 duplicates) are skipped
            raise ValueError(f"no openable assets with role 'data'; pass assets=... Available: {listing}")
    else:
        keys = [assets] if isinstance(assets, str) else list(assets)
        if missing := [k for k in keys if k not in avail]:
            raise KeyError(f"assets {missing} not found. Available: {listing}")
        if not keys:  # e.g. an open_settings() widget with nothing ticked
            raise ValueError(f"no assets selected. Available: {listing}")
    if opener is None:
        names = {k: _match(avail[k]) for k in keys}
        if None in names.values():
            bad = ", ".join(_fmt(k, avail[k]) for k, n in names.items() if n is None)
            raise ValueError(f"no opener for {bad}. Registered: {', '.join(_REGISTRY)}; or pass opener=...")
        if len(set(names.values())) > 1:
            fmts = "; ".join(f"{_fmt(k, avail[k])} -> {n}" for k, n in names.items())
            raise ValueError(f"assets resolve to different openers: {fmts}. Pass assets=... with one format.")
        opener = next(iter(names.values()))
    elif opener not in _REGISTRY:
        raise ValueError(f"unknown opener {opener!r}. Registered: {', '.join(_REGISTRY)}")
    return opener, keys


def open_assets(owners: list, assets=None, opener: str | None = None, **kwargs):
    """Resolve assets across ``owners`` to one opener and call it (see module docstring)."""
    opener, keys = resolve(owners, assets, opener)
    return _coerce(_REGISTRY[opener]["fn"](owners, keys, **kwargs), opener)


def _avail(owners: list) -> dict[str, pystac.Asset]:
    avail: dict[str, pystac.Asset] = {}
    for o in owners:
        for k, a in o.assets.items():
            avail.setdefault(k, a)
    return avail


def options(owners: list) -> list[dict]:
    """One entry per asset key: label, matched opener (or None), whether it has the 'data' role, and band
    common names (``eo:bands``/``bands``) for display and filtering."""
    _load_plugins()
    return [
        dict(key=k, label=_fmt(k, a), opener=_match(a), data="data" in (a.roles or []), title=a.title or "",
             bands=list(dict.fromkeys(b["common_name"] for f in ("eo:bands", "bands")
                                      for b in a.extra_fields.get(f) or [] if b.get("common_name"))))
        for k, a in _avail(owners).items()
    ]


def opener_params(name: str, owners: list, assets: list[str]) -> dict[str, dict]:
    """JSON-serialisable spec ``{kind, choices?, default, inferred}`` for each param of opener ``name``."""
    _load_plugins()
    if name not in _REGISTRY:
        raise ValueError(f"unknown opener {name!r}. Registered: {', '.join(_REGISTRY)}")
    out = {}
    for pname, p in _REGISTRY[name]["params"].items():
        d, inferred = p.default, callable(p.default)
        if inferred:
            try:
                d = d(owners, assets)
            except Exception:
                d = None
        kind = p.kind
        if callable(kind) and not isinstance(kind, type):
            try:
                kind = list(kind(owners, assets)) or str
            except Exception:
                kind = str  # choices unavailable: fall back to free text
        spec = {"kind": "choice", "choices": list(kind)} if isinstance(kind, (list, tuple)) else {"kind": kind.__name__}
        out[pname] = {**spec, "default": d, "inferred": inferred}
    return out


# ----------------------------------------------------------------- built-in openers
def _need(mod: str, extra: str):
    try:
        return __import__(mod, fromlist=["_"])
    except ImportError as e:
        raise ImportError(f"{mod} is required to open this format: pip install \"stac-viewer[{extra}]\"") from e


def _combine(dss: list):
    xr = _need("xarray", "open")
    return _tag_lonlat(dss[0] if len(dss) == 1 else xr.combine_by_coords(dss))


_LONLAT = {"X": ("lon", "degrees_east", "longitude"), "Y": ("lat", "degrees_north", "latitude")}


def _tag_lonlat(ds):
    """Give lon/lat dimension coords CF ``axis``/``standard_name`` attrs so rioxarray finds them,
    and EPSG:4326 when there is no CRS (if rioxarray is installed)."""
    tagged = set()
    for axis, (name, units, standard_name) in _LONLAT.items():
        for dim in ds.dims:
            c = ds.coords.get(dim)
            if c is None or (dim != name and c.attrs.get("units") != units):
                continue
            c.attrs.setdefault("axis", axis)
            c.attrs.setdefault("standard_name", standard_name)
            tagged.add(axis)
    if tagged == {"X", "Y"}:
        try:
            import rioxarray  # noqa: F401
        except ImportError:
            return ds
        if ds.rio.crs is None:
            ds = ds.rio.write_crs("EPSG:4326")
    return ds


def _pairs(items: list, assets: list[str]):
    return [(i, i.assets[k]) for i in items for k in assets if k in i.assets]


def _infer(items, assets, find):
    """Values ``find(item, asset)`` returns (when not None) for each selected asset of each item."""
    vals = [find(i, a) for i, a in _pairs(items, assets)]
    return [v for v in vals if v is not None]


def _first(item, asset, key):
    """First of the asset's, then the item's ``key`` field that is set."""
    for d in (asset.extra_fields, getattr(item, "properties", {})):
        if d.get(key) is not None:
            return d[key]


def _infer_crs(items, assets):
    def find(i, a):
        if (code := _first(i, a, "proj:code")) is not None:
            return str(code)
        if (n := _first(i, a, "proj:epsg")) is not None:
            return f"EPSG:{n}"

    found = _infer(items, assets, find)
    return Counter(found).most_common(1)[0][0] if found else None


def _infer_resolution(items, assets):
    def find(i, a):
        bands = a.extra_fields.get("raster:bands") or [{}]
        if (r := bands[0].get("spatial_resolution")) is not None or (r := _first(i, a, "gsd")) is not None:
            return float(r)
        if (t := _first(i, a, "proj:transform")) is not None:
            return abs(float(t[0]))

    found = _infer(items, assets, find)
    return min(found) if found else None


@register_opener(
    "cog",
    media_types=[
        "image/tiff; application=geotiff; profile=cloud-optimized",
        "image/tiff; application=geotiff",
        "image/tiff",
    ],
    extensions=[".tif", ".tiff"],
    params={
        "crs": Param(str, _infer_crs),
        "resolution": Param(float, _infer_resolution),
        "resampling": Param(["nearest", "bilinear", "cubic", "average", "mode", "min", "max"], "nearest"),
        "groupby": Param(["time", "solar_day", "id"], "time"),  # odc.stac.load's default is "time"
    },
)
def _open_cog(items, assets, **kwargs):
    odc = _need("odc.stac", "cog")
    if not all(isinstance(i, pystac.Item) for i in items):
        raise TypeError("the cog opener needs items; collection-level assets are not supported")
    return odc.load(items, bands=assets, **{"chunks": {}, **kwargs})


@register_opener(
    "zarr",
    media_types=["application/vnd+zarr", "application/vnd.zarr", "application/x-zarr"],
    extensions=[".zarr"],
)
def _open_zarr(items, assets, **kwargs):
    xr = _need("xarray", "zarr")
    _need("xpystac", "zarr")
    dss = []
    for _, a in _pairs(items, assets):
        # xpystac dispatches on media_type, so force it for assets matched by extension
        za = pystac.Asset(a.href, a.title, a.description, "application/vnd+zarr", a.roles, a.extra_fields)
        dss.append(xr.open_dataset(za, engine="stac", **kwargs))
    return _combine(dss)


@register_opener(
    "netcdf",
    media_types=["application/x-netcdf", "application/netcdf", "application/x-hdf5", "application/x-hdf"],
    extensions=[".nc", ".nc4", ".h5", ".hdf5"],
)
def _open_netcdf(items, assets, **kwargs):
    xr = _need("xarray", "netcdf")
    _need("h5netcdf", "netcdf")
    dss = []
    for _, a in _pairs(items, assets):  # xpystac has no netcdf support, so open directly
        src = a.href
        if urlparse(src).scheme not in ("", "file"):  # remote: open a file-like object
            src = _need("fsspec", "netcdf").open(src).open()
        elif src.startswith("file:"):
            src = urlparse(src).path
        dss.append(xr.open_dataset(src, **{"engine": "h5netcdf", **kwargs}))
    return _combine(dss)


@register_opener(
    "geoparquet",
    media_types=["application/vnd.apache.parquet", "application/x-parquet"],
    extensions=[".parquet", ".geoparquet"],
    params={"columns": Param(str)},  # comma-separated
)
def _open_geoparquet(items, assets, **kwargs):
    gpd = _need("geopandas", "parquet")
    pd = _need("pandas", "parquet")
    if isinstance(cols := kwargs.pop("columns", None), str):
        cols = [c.strip() for c in cols.split(",") if c.strip()]
    kwargs |= {"columns": list(cols)} if cols else {}
    out = []
    for _, a in _pairs(items, assets):
        try:
            out.append(gpd.read_parquet(a.href, **kwargs))
        except ValueError:  # no geo metadata
            out.append(pd.read_parquet(a.href, **kwargs))
    return out[0] if len(out) == 1 else pd.concat(out, ignore_index=True)


def _gdb_layers(items, assets):
    """``[(name, geometry type or None)]`` of the first selected geodatabase / GeoPackage."""
    pyogrio = _need("pyogrio", "gdb")
    return [tuple(r) for r in pyogrio.list_layers(_pairs(items, assets)[0][1].href)]


def _gdb_default_layer(items, assets):
    """First layer with geometry; GDAL's default is the first layer, often a metadata table."""
    layers = _gdb_layers(items, assets)
    return next((n for n, g in layers if g), layers[0][0] if layers else None)


@register_opener(
    "gpkg",
    media_types=["application/geopackage+sqlite3"],
    extensions=[".gpkg"],
    params={"layer": Param(lambda i, a: [n for n, _ in _gdb_layers(i, a)], _gdb_default_layer)},
)
@register_opener(
    "filegdb",
    media_types=["application/x.filegdb+zip"],  # used by data.geo.admin.ch
    extensions=[".gdb.zip"],
    params={"layer": Param(lambda i, a: [n for n, _ in _gdb_layers(i, a)], _gdb_default_layer)},
)
def _open_filegdb(items, assets, **kwargs):
    gpd = _need("geopandas", "gdb")
    pd = _need("pandas", "gdb")
    _need("pyogrio", "gdb")  # GDAL reads GeoPackages and zipped geodatabases directly
    pairs = _pairs(items, assets)
    if kwargs.get("layer") is None:
        kwargs["layer"] = _gdb_default_layer(items, assets)
    out = [gpd.read_file(a.href, **kwargs) for _, a in pairs]
    return out[0] if len(out) == 1 else pd.concat(out, ignore_index=True)


@register_opener("geojson", media_types=["application/geo+json"], extensions=[".geojson"])
def _open_geojson(items, assets, **kwargs):
    gpd = _need("geopandas", "gdb")
    pd = _need("pandas", "gdb")
    out = [gpd.read_file(a.href, **kwargs) for _, a in _pairs(items, assets)]
    return out[0] if len(out) == 1 else pd.concat(out, ignore_index=True)


_CSV_X, _CSV_Y = ("lon", "longitude", "lng", "long", "x", "easting"), ("lat", "latitude", "y", "northing")


_CSV_SEPS = (",", ";", "\t", "|")


def _csv_sniff(href: str) -> str:
    """Separator occurring most often in the header line (``,`` if none does)."""
    pd = _need("pandas", "csv")
    head = pd.read_csv(href, sep="\x01", header=None, nrows=1, quoting=3, dtype=str).iat[0, 0]  # whole first line
    n = {s: str(head).count(s) for s in _CSV_SEPS}
    return max(n, key=n.get) if any(n.values()) else ","


def _csv_read(href: str, **kwargs):
    pd = _need("pandas", "csv")
    if kwargs.get("sep") is None:
        kwargs["sep"] = _csv_sniff(href)
    return pd.read_csv(href, **kwargs)


def _csv_columns(items, assets):
    """Header of the first selected CSV."""
    return list(_csv_read(_pairs(items, assets)[0][1].href, nrows=0).columns)


def _csv_coord(names):
    def default(items, assets):
        cols = {c.lower(): c for c in _csv_columns(items, assets)}
        return next((cols[n] for n in names if n in cols), None)

    return default


@register_opener(
    "csv",
    media_types=["text/csv", "application/csv", "text/tab-separated-values"],
    extensions=[".csv", ".csv.gz", ".tsv", ".tsv.gz"],
    params={
        "sep": Param(list(_CSV_SEPS), lambda i, a: _csv_sniff(_pairs(i, a)[0][1].href)),  # not passed: sniffed per file
        "x": Param(_csv_columns, _csv_coord(_CSV_X)),  # x and y set: GeoDataFrame of points
        "y": Param(_csv_columns, _csv_coord(_CSV_Y)),
        "crs": Param(str, lambda i, a: _infer_crs(i, a) or "EPSG:4326"),
    },
)
def _open_csv(items, assets, x=..., y=..., crs=None, **kwargs):
    pd = _need("pandas", "csv")
    x = _csv_coord(_CSV_X)(items, assets) if x is ... else x  # not passed: infer; None: plain DataFrame
    y = _csv_coord(_CSV_Y)(items, assets) if y is ... else y
    out = []
    for i, a in _pairs(items, assets):
        df = _csv_read(a.href, **kwargs)
        g = getattr(i, "geometry", None) or {}
        if not (x and y) and g.get("type") == "Point":  # e.g. one station per item: keep its location
            gpd = _need("geopandas", "csv")
            lon, lat = g["coordinates"][:2]
            df = gpd.GeoDataFrame(df.assign(item_id=i.id), geometry=gpd.points_from_xy([lon] * len(df), [lat] * len(df)),
                                  crs="EPSG:4326")
        out.append(df)
    df = out[0] if len(out) == 1 else pd.concat(out, ignore_index=True)
    if x and y:
        gpd = _need("geopandas", "csv")
        df = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[x], df[y]), crs=crs or "EPSG:4326")
    return df

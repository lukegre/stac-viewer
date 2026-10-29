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
from typing import Callable
from urllib.parse import urlparse

import pystac

_REGISTRY: dict[str, dict] = {}
_loaded = False


def _norm(t: str) -> str:
    return t.lower().replace(" ", "")


def register_opener(name: str, media_types=(), extensions=()):
    """Decorator registering ``f(items, assets, **kwargs)`` as opener ``name``."""

    def deco(fn: Callable):
        _REGISTRY.pop(name, None)  # re-registering moves it to the end (highest priority)
        mt = [_norm(t) for t in media_types]
        _REGISTRY[name] = dict(
            fn=fn, exact=set(mt), base={t.split(";")[0] for t in mt}, ext={e.lower() for e in extensions}
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
    steps = [("exact", t), ("base", t.split(";")[0]), ("ext", _ext(asset.href))]
    for field, val in steps:
        if val:
            for name, o in reversed(_REGISTRY.items()):
                if val in o[field]:
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


def open_assets(owners: list, assets=None, opener: str | None = None, **kwargs):
    """Resolve assets across ``owners`` to one opener and call it (see module docstring)."""
    _load_plugins()
    avail: dict[str, pystac.Asset] = {}
    for o in owners:
        for k, a in o.assets.items():
            avail.setdefault(k, a)
    listing = ", ".join(_fmt(k, a) for k, a in avail.items()) or "none"
    if assets is None:
        keys = [k for k, a in avail.items() if "data" in (a.roles or []) and (opener or _match(a))]
        if not keys:  # data assets without an opener (e.g. JPEG2000 duplicates) are skipped
            raise ValueError(f"no openable assets with role 'data'; pass assets=... Available: {listing}")
    else:
        keys = [assets] if isinstance(assets, str) else list(assets)
        if missing := [k for k in keys if k not in avail]:
            raise KeyError(f"assets {missing} not found. Available: {listing}")
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
    return _coerce(_REGISTRY[opener]["fn"](owners, keys, **kwargs), opener)


# ----------------------------------------------------------------- built-in openers
def _need(mod: str, extra: str):
    try:
        return __import__(mod, fromlist=["_"])
    except ImportError as e:
        raise ImportError(f"{mod} is required to open this format: pip install \"stac-viewer[{extra}]\"") from e


def _combine(dss: list):
    xr = _need("xarray", "open")
    return dss[0] if len(dss) == 1 else xr.combine_by_coords(dss)


def _pairs(items: list, assets: list[str]):
    return [(i, i.assets[k]) for i in items for k in assets if k in i.assets]


@register_opener(
    "cog",
    media_types=[
        "image/tiff; application=geotiff; profile=cloud-optimized",
        "image/tiff; application=geotiff",
        "image/tiff",
    ],
    extensions=[".tif", ".tiff"],
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
)
def _open_geoparquet(items, assets, **kwargs):
    gpd = _need("geopandas", "parquet")
    pd = _need("pandas", "parquet")
    out = []
    for _, a in _pairs(items, assets):
        try:
            out.append(gpd.read_parquet(a.href, **kwargs))
        except ValueError:  # no geo metadata
            out.append(pd.read_parquet(a.href, **kwargs))
    return out[0] if len(out) == 1 else pd.concat(out, ignore_index=True)

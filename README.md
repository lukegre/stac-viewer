# stac-viewer

Browse STAC catalogs in Jupyter with dot notation, HTML viewers, an interactive bbox/date selector and search.

## Install

```bash
pip install stac-viewer             # core
pip install "stac-viewer[widget]"   # + interactive selector() (anywidget)
pip install "stac-viewer[open]"     # + open() for all formats (or [cog], [zarr], [netcdf], [parquet], [gdb])
```

## Usage

```python
from stac_viewer import browse

cat = browse("https://earth-search.aws.element84.com/v1")  # API URL, catalog.json path/URL, or pystac object
cat                                                         # table of collections
col = cat.sentinel_2_l2a                                    # HTML card: extent map, temporal bar, metadata
sel = col.selector(); sel                                   # draw bbox/polygon, pick dates, cloud cover + filters (needs the widget extra)
items = col.search(sel)                                     # uses the selection -> Items (HTML summary)
items = col.search(bbox=[8.4, 47.3, 8.7, 47.5], datetime="2026-06-01/2026-06-30", max_items=50)
```

## Opening data

`items.open(assets)` and `col.open(assets)` (collection-level assets) return an `xarray.Dataset`, or a
`pandas`/`geopandas` DataFrame for parquet and File Geodatabases. `assets` is a key or list of keys; by default all `data`-role assets
that an opener can handle. Extra kwargs go to the backend.

```python
ds = items.open(["red", "nir"], resolution=20, crs="EPSG:32632")  # COG -> odc.stac.load
ds = col.open("zarr-https")                                       # collection-level Zarr -> xpystac
ds = items.open("data", opener="netcdf")                          # force an opener
```

| opener | media types / extensions | backend | several items |
|---|---|---|---|
| `cog` | `image/tiff…`, `.tif` | `odc.stac.load` (lazy, `chunks={}`) | stacked along time |
| `zarr` | `application/vnd+zarr`, `.zarr` | xpystac (`engine="stac"`) | `xr.combine_by_coords` |
| `netcdf` | `application/x-netcdf`, `x-hdf5`, `.nc`, `.h5` | `xr.open_dataset(engine="h5netcdf")` (+ fsspec) | `xr.combine_by_coords` |
| `geoparquet` | `application/vnd.apache.parquet`, `.parquet` | `geopandas.read_parquet` | `pd.concat` |
| `filegdb` | `.gdb.zip` (zipped Esri File Geodatabase) | `geopandas.read_file` (pyogrio; `layer=...`) | `pd.concat` |

Assets are matched by exact media type, then base type, then file extension. Add your own (later registrations
win, so this can override a built-in):

```python
from stac_viewer import register_opener

@register_opener("grib", media_types=["application/x-grib"], extensions=[".grib2"])
def open_grib(items, assets, **kwargs):  # items: pystac Items (or [Collection] for col.open)
    ...                                   # must return xr.Dataset / xr.DataArray / pd.DataFrame
```

Packages can also ship openers via an entry point in the `stac_viewer.openers` group; the module is imported on
first `open()` and should call `register_opener`.

Extra keyword arguments to `browse` go to `pystac_client.Client.open`, e.g.
`browse(url, modifier=planetary_computer.sign_inplace)`.

## APIs vs static catalogs

- **STAC APIs** (with item-search): server-side search; all `pystac_client` search kwargs
  (`query`, `filter`, `sortby`, ...) are passed through.
- **Static catalogs** (`catalog.json`): collections are found recursively and items are filtered
  client-side. Only `bbox`, `intersects`, `datetime`, `ids`, `query` and `max_items` are supported; anything else
  raises `TypeError`. `intersects` is approximated by the bounding box of the geometry, and `query` supports
  `lt`, `lte`, `gt`, `gte`, `eq`, `neq` (items without the property are excluded).

`datetime` accepts `"a/b"`, `"a"`, open ends (`"../b"`), `datetime`/`date` objects, or a `(start, end)` tuple.
`search()` returns `max_items=1000` items by default; pass `max_items=None` for no limit.

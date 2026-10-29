import numpy as np
import pystac
import pytest

import stac_viewer
from stac_viewer import Items, openers
from test_core import D, make_collection, make_item

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")


@pytest.fixture(autouse=True)
def registry():
    saved = dict(openers._REGISTRY)
    yield
    openers._REGISTRY.clear()
    openers._REGISTRY.update(saved)


def item_with(id, when, **assets):
    it = make_item(id, (0, 0, 1, 1), when)
    for k, (href, typ) in assets.items():
        it.add_asset(k, pystac.Asset(href, media_type=typ, roles=["data"]))
    return it


def test_cog(tmp_path):
    pytest.importorskip("odc.stac")
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    items = []
    for n, when in enumerate([D(2020, 1, 1), D(2020, 2, 1)]):
        path = tmp_path / f"b{n}.tif"
        tr = from_origin(0, 1, 0.1, 0.1)
        with rasterio.open(path, "w", driver="GTiff", width=10, height=10, count=1, dtype="uint8", crs="EPSG:4326", transform=tr) as f:
            f.write(np.full((1, 10, 10), n + 1, "uint8"))
        it = item_with(f"c{n}", when, red=(str(path), "image/tiff; application=geotiff; profile=cloud-optimized"))
        it.stac_extensions.append("https://stac-extensions.github.io/projection/v1.1.0/schema.json")
        it.properties.update({"proj:epsg": 4326, "proj:shape": [10, 10], "proj:transform": list(tr)[:6]})
        items.append(it)
    ds = Items(items).open()
    assert isinstance(ds, xr.Dataset) and ds.sizes["time"] == 2 and "red" in ds
    assert ds.red.isel(time=1).mean().compute() == 2


def test_zarr(tmp_path):
    pytest.importorskip("xpystac")
    pytest.importorskip("zarr")
    xr.Dataset({"t": (("x",), np.arange(4.0))}, coords={"x": np.arange(4)}).to_zarr(tmp_path / "a.zarr")
    ds = Items([item_with("z", D(2020), d=(str(tmp_path / "a.zarr"), "application/vnd+zarr"))]).open()
    assert list(ds.t.values) == [0, 1, 2, 3]


def test_netcdf_combines_and_matches_by_extension(tmp_path):
    pytest.importorskip("h5netcdf")
    items = []
    for n in range(2):
        p = tmp_path / f"t{n}.nc"
        xr.Dataset({"v": (("time",), [float(n)])}, coords={"time": [n]}).to_netcdf(p, engine="h5netcdf")
        items.append(item_with(f"n{n}", D(2020, 1 + n), d=(str(p), None)))  # no media type
    ds = Items(items).open()
    assert list(ds.v.values) == [0.0, 1.0]


def test_geoparquet(tmp_path):
    gpd = pytest.importorskip("geopandas")
    pytest.importorskip("pyarrow")
    from shapely.geometry import Point

    paths = []
    for n in range(2):
        p = tmp_path / f"g{n}.parquet"
        gpd.GeoDataFrame({"a": [n]}, geometry=[Point(n, n)], crs=4326).to_parquet(p)
        paths.append(p)
    plain = tmp_path / "plain.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(plain)
    it = lambda i, p: item_with(f"p{i}", D(2020), d=(str(p), "application/vnd.apache.parquet"))
    df = Items([it(0, paths[0]), it(1, paths[1])]).open()
    assert isinstance(df, gpd.GeoDataFrame) and len(df) == 2
    assert type(Items([it(2, plain)]).open()) is pd.DataFrame


def test_filegdb_zip(tmp_path):
    gpd = pytest.importorskip("geopandas")
    pytest.importorskip("pyogrio")
    import shutil
    from shapely.geometry import Point

    g = gpd.GeoDataFrame({"a": [1.0, 2.0]}, geometry=[Point(0, 0), Point(1, 1)], crs=4326)
    g.to_file(tmp_path / "t.gdb", layer="pts", driver="OpenFileGDB")
    g.iloc[:1].to_file(tmp_path / "t.gdb", layer="one", driver="OpenFileGDB")
    shutil.make_archive(str(tmp_path / "t.gdb"), "zip", root_dir=tmp_path, base_dir="t.gdb")
    it = item_with("g", D(2020), d=(str(tmp_path / "t.gdb.zip"), "application/zip"))
    df = Items([it]).open(layer="one")
    assert isinstance(df, gpd.GeoDataFrame) and len(df) == 1

def test_opener_override_and_user_registration():
    it = item_with("a", D(2020), d=("x.nc", "application/x-netcdf"))
    calls = []

    @stac_viewer.register_opener("mine", media_types=["application/x-netcdf"])
    def mine(items, assets, **kw):
        calls.append((assets, kw))
        return xr.Dataset({"z": (("x",), [1])})

    assert "z" in Items([it]).open(foo=1)  # later registration beats built-in netcdf
    assert calls == [(["d"], {"foo": 1})]
    stac_viewer.register_opener("other")(lambda i, a, **k: xr.Dataset())
    assert not Items([it]).open(opener="other").data_vars
    with pytest.raises(ValueError, match="unknown opener"):
        Items([it]).open(opener="nope")


def test_dataarray_wrapped_and_bad_return():
    it = item_with("a", D(2020), d=("x.foo", "application/x-foo"))
    stac_viewer.register_opener("foo", media_types=["application/x-foo"])(lambda i, a: xr.DataArray([1, 2], dims="x"))
    assert list(Items([it]).open().data_vars) == ["data"]
    stac_viewer.register_opener("foo", media_types=["application/x-foo"])(lambda i, a: xr.DataArray([1], dims="x", name="q"))
    assert list(Items([it]).open().data_vars) == ["q"]
    stac_viewer.register_opener("foo", media_types=["application/x-foo"])(lambda i, a: [1])
    with pytest.raises(TypeError, match="foo"):
        Items([it]).open()


def test_default_skips_data_assets_without_opener():
    it = item_with("a", D(2020), d=("x.foo", "application/x-foo"), j=("x.jp2", "image/jp2"))
    seen = []
    stac_viewer.register_opener("foo", media_types=["application/x-foo"])(lambda i, a: seen.append(a) or xr.Dataset())
    Items([it]).open()
    assert seen == [["d"]]


def test_errors():
    it = item_with("a", D(2020), nc=("a.nc", None), pq=("a.parquet", None), odd=("a.xyz", None))
    with pytest.raises(ValueError, match="different openers") as e:
        Items([it]).open(["nc", "pq"])
    assert "nc (nc)" not in str(e.value) and "netcdf" in str(e.value) and "geoparquet" in str(e.value)
    with pytest.raises(KeyError, match="missing"):
        Items([it]).open("missing")
    with pytest.raises(ValueError, match="no opener"):
        Items([it]).open("odd")
    bare = make_item("b", (0, 0, 1, 1), D(2020))
    bare.add_asset("x", pystac.Asset("x.nc"))
    with pytest.raises(ValueError, match="role 'data'"):
        Items([bare]).open()


@pytest.mark.parametrize("href", ["s3://b/a.zarr/", "https://h/a.zarr?sig=1"])
def test_extension_match(href):
    assert openers._match(pystac.Asset(href)) == "zarr"
    assert openers._match(pystac.Asset(href.replace(".zarr", ".gdb.zip").replace("zip/", "zip"))) == "filegdb"


def test_collection_open_zarr(tmp_path):
    pytest.importorskip("xpystac")
    pytest.importorskip("zarr")
    xr.Dataset({"t": (("x",), np.arange(3.0))}, coords={"x": np.arange(3)}).to_zarr(tmp_path / "c.zarr")
    col = make_collection("c")
    col.add_asset("cube", pystac.Asset(str(tmp_path / "c.zarr"), media_type="application/vnd+zarr", roles=["data"]))
    ds = stac_viewer.CollectionView(col, None).open()
    assert list(ds.t.values) == [0, 1, 2]

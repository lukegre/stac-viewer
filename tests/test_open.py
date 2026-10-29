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
    gpd.GeoDataFrame({"s": ["ok"]}).to_file(tmp_path / "t.gdb", layer="meta", driver="OpenFileGDB")  # no geometry
    g.to_file(tmp_path / "t.gdb", layer="pts", driver="OpenFileGDB")
    g.iloc[:1].to_file(tmp_path / "t.gdb", layer="one", driver="OpenFileGDB")
    shutil.make_archive(str(tmp_path / "t.gdb"), "zip", root_dir=tmp_path, base_dir="t.gdb")
    it = item_with("g", D(2020), d=(str(tmp_path / "t.gdb.zip"), "application/zip"))
    df = Items([it]).open(layer="one")
    assert isinstance(df, gpd.GeoDataFrame) and len(df) == 1
    assert len(Items([it]).open()) == 2  # default: first layer with geometry, not GDAL's first layer
    spec = openers.opener_params("filegdb", [it], ["d"])["layer"]
    assert spec["kind"] == "choice" and spec["choices"] == ["meta", "pts", "one"] and spec["default"] == "pts"


def test_csv(tmp_path):
    gpd = pytest.importorskip("geopandas")
    for n in range(2):
        pd.DataFrame({"Lon": [n, n + 0.5], "Lat": [n, n], "v": [n, n]}).to_csv(tmp_path / f"c{n}.csv", index=False)
    pd.DataFrame({"a": [1], "b": [2]}).to_csv(tmp_path / "t.tsv.gz", sep="\t", index=False)
    it = lambda i, p, t=None: item_with(f"c{i}", D(2020), d=(str(tmp_path / p), t))
    items = [it(0, "c0.csv", "text/csv"), it(1, "c1.csv")]
    df = Items(items).open()
    assert isinstance(df, gpd.GeoDataFrame) and len(df) == 4 and df.crs == "EPSG:4326" and df.geometry.x[1] == 0.5
    spec = openers.opener_params("csv", items, ["d"])
    assert spec["x"]["choices"] == ["Lon", "Lat", "v"] and (spec["x"]["default"], spec["y"]["default"]) == ("Lon", "Lat")
    assert type(Items(items).open(x=None)) is pd.DataFrame
    tsv = Items([it(2, "t.tsv.gz")])
    assert list(tsv.open().columns) == ["a", "b"] and openers.opener_params("csv", list(tsv), ["d"])["x"]["default"] is None


def test_csv_station_items_keep_location(tmp_path):
    gpd = pytest.importorskip("geopandas")
    items = []
    for n, (lon, lat) in enumerate([(7.4, 46.9), (8.5, 47.4)]):
        pd.DataFrame({"time": [1, 2], "t": [n, n]}).to_csv(tmp_path / f"s{n}.csv", sep=";", index=False)
        it = item_with(f"st{n}", D(2020), d=(str(tmp_path / f"s{n}.csv"), "text/csv"))
        it.geometry, it.bbox = {"type": "Point", "coordinates": [lon, lat]}, [lon, lat, lon, lat]
        items.append(it)
    df = Items(items).open()
    assert isinstance(df, gpd.GeoDataFrame) and df.crs == "EPSG:4326" and len(df) == 4
    assert list(df.item_id) == ["st0", "st0", "st1", "st1"] and list(df.geometry.x) == [7.4, 7.4, 8.5, 8.5]


def test_csv_separators(tmp_path):
    for sep, name in [(";", "semi.csv"), (",", "comma.csv"), ("|", "pipe.csv")]:
        pd.DataFrame({"a": ["x,y", "z"], "b": [1, 2]}).to_csv(tmp_path / name, sep=sep, index=False)
        it = Items([item_with("s", D(2020), d=(str(tmp_path / name), "text/csv"))])
        assert openers.opener_params("csv", list(it), ["d"])["sep"]["default"] == sep
        df = it.open()
        assert list(df.columns) == ["a", "b"] and list(df.a) == ["x,y", "z"]
    assert list(it.open(sep=",").columns) == ["a|b"]  # explicit sep wins


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
    with pytest.raises(ValueError, match="no assets selected"):
        Items([it]).open([])
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


def test_resolve_and_options():
    it = item_with("a", D(2020), nc=("a.nc", None), odd=("a.xyz", None))
    it.assets["meta"] = pystac.Asset("m.json")
    assert openers.resolve([it]) == ("netcdf", ["nc"])
    assert openers.resolve([it], "nc", "zarr") == ("zarr", ["nc"])
    with pytest.raises(ValueError, match="unknown opener"):
        openers.resolve([it], opener="nope")
    opts = {o["key"]: o for o in openers.options([it, it])}
    assert list(opts) == ["nc", "odd", "meta"]
    assert opts["nc"]["opener"] == "netcdf" and opts["nc"]["data"] and opts["nc"]["label"].startswith("nc (")
    assert opts["odd"]["opener"] is None and not opts["meta"]["data"]
    it.assets["nc"].title = "Red band"
    it.assets["nc"].extra_fields.update({"eo:bands": [{"name": "B04", "common_name": "red"}], "bands": [{"common_name": "red"}]})
    nc = openers.options([it])[0]
    assert nc["title"] == "Red band" and nc["bands"] == ["red"] and opts["odd"]["bands"] == []


def cog_item(id, asset_extra=None, **props):
    it = item_with(id, D(2020), red=("r.tif", "image/tiff"), nir=("n.tif", "image/tiff"))
    it.assets["red"].extra_fields.update(asset_extra or {})
    it.properties.update(props)
    return it


def test_cog_params_crs_majority():
    items = [
        cog_item("a", {"proj:code": "EPSG:32633"}),
        cog_item("b", **{"proj:code": "EPSG:32633"}),
        cog_item("c", **{"proj:epsg": 32632}),
        cog_item("d", {"proj:epsg": 32632}),
        cog_item("e", **{"proj:epsg": 32633}),
    ]
    p = openers.opener_params("cog", items, ["red"])
    assert p["crs"] == {"kind": "str", "default": "EPSG:32633", "inferred": True}
    assert p["resampling"]["default"] == "nearest" and p["resampling"]["kind"] == "choice"
    assert p["groupby"]["choices"] == ["time", "solar_day", "id"] and p["groupby"]["default"] == "time"
    none = openers.opener_params("cog", [cog_item("x")], ["red"])
    assert none["crs"]["default"] is None and none["resolution"] == {"kind": "float", "default": None, "inferred": True}


def test_cog_params_resolution():
    band = {"raster:bands": [{"spatial_resolution": 20}]}
    a = cog_item("a", band, gsd=10, **{"proj:transform": [5, 0, 0, 0, -5, 0]})
    assert openers.opener_params("cog", [a], ["red"])["resolution"]["default"] == 20.0
    b = cog_item("b", gsd=10, **{"proj:transform": [5, 0, 0, 0, -5, 0]})
    assert openers.opener_params("cog", [b], ["red"])["resolution"]["default"] == 10.0
    c = cog_item("c", {"proj:transform": [-30, 0, 0, 0, 30, 0]})
    assert openers.opener_params("cog", [c], ["red"])["resolution"]["default"] == 30.0
    c.assets["nir"].extra_fields["gsd"] = 10
    assert openers.opener_params("cog", [c], ["red", "nir"])["resolution"]["default"] == 10.0  # finest wins


def test_custom_opener_params():
    def boom(items, assets):
        raise RuntimeError

    @stac_viewer.register_opener(
        "mine",
        media_types=["application/x-foo"],
        params={
            "n": openers.Param(int, lambda items, assets: len(items) + len(assets)),
            "bad": openers.Param(float, boom),
            "mode": openers.Param(("a", "b"), "b"),
        },
    )
    def mine(items, assets, **kw):
        return xr.Dataset()

    it = item_with("a", D(2020), d=("x.foo", "application/x-foo"))
    assert openers.opener_params("mine", [it], ["d"]) == {
        "n": {"kind": "int", "default": 2, "inferred": True},
        "bad": {"kind": "float", "default": None, "inferred": True},
        "mode": {"kind": "choice", "choices": ["a", "b"], "default": "b", "inferred": False},
    }
    assert openers.opener_params("netcdf", [it], ["d"]) == {}
    with pytest.raises(ValueError, match="unknown opener"):
        openers.opener_params("nope", [it], ["d"])


def test_geoparquet_columns_string(tmp_path):
    pytest.importorskip("pyarrow")
    p = tmp_path / "t.parquet"
    pd.DataFrame({"a": [1], "b": [2], "c": [3]}).to_parquet(p)
    it = Items([item_with("p", D(2020), d=(str(p), "application/vnd.apache.parquet"))])
    assert list(it.open(columns="a, c,").columns) == ["a", "c"]
    assert list(it.open(columns=["b"]).columns) == ["b"]
    assert list(it.open(columns="").columns) == ["a", "b", "c"]
    assert list(it.open(columns=None).columns) == ["a", "b", "c"]


def cog_items():
    its = [cog_item(i, {"proj:code": "EPSG:32633"}, gsd=10) for i in "ab"]
    for it in its:
        it.add_asset("nc", pystac.Asset("x.nc", media_type="application/x-netcdf"))
        it.add_asset("odd", pystac.Asset("x.xyz"))
    return Items(its)


def test_open_settings_defaults_and_code():
    w = cog_items().open_settings()
    assert w.assets == ["red", "nir"] and w.opener == "cog" and w.error == ""
    assert [(o["key"], o["opener"]) for o in w.asset_options] == [("red", "cog"), ("nir", "cog"), ("nc", "netcdf"), ("odd", None)]
    assert w.values == {"crs": "EPSG:32633", "resolution": 10.0, "resampling": "nearest", "groupby": "time"}
    assert w.kwargs() == {"crs": "EPSG:32633", "resolution": 10.0}  # inferred pinned, static defaults omitted
    assert w.code == "open(\n  assets=['red', 'nir'],\n  crs='EPSG:32633',\n  resolution=10.0,\n)"
    w.values = {**w.values, "groupby": "solar_day", "resolution": "20"}
    assert w.kwargs() == {"crs": "EPSG:32633", "resolution": 20.0, "groupby": "solar_day"}
    assert "groupby='solar_day'" in w.code and "resolution=20.0" in w.code
    w.values = {**w.values, "crs": ""}
    assert "crs" not in w.kwargs()
    w.values = {**w.values, "resolution": "abc"}
    assert "resolution" in w.error


def test_open_settings_switch_opener_and_keep_edits():
    w = cog_items().open_settings()
    w.values = {**w.values, "resampling": "cubic"}
    w.assets = ["red"]  # same opener: edits kept, inferred values recomputed
    assert w.values["resampling"] == "cubic" and w.values["crs"] == "EPSG:32633"
    w.assets = ["nc"]
    assert w.opener == "netcdf" and w.params == {} and w.values == {}
    assert w.code == "open(\n  assets=['nc'],\n)"
    w.assets = ["red", "nir"]
    assert w.opener == "cog" and w.values["resampling"] == "nearest"
    w.assets = ["red", "nc"]  # mixed openers: error, previous state kept
    assert "different openers" in w.error and w.opener == "cog"
    w.assets = []
    assert w.opener == "" and w.code == "" and w.error == ""


def test_open_with_settings_widget():
    seen = {}

    @stac_viewer.register_opener("cap", media_types=["application/x-cap"], params={"n": openers.Param(int, lambda i, a: 3)})
    def cap(items, assets, **kw):
        seen.update(assets=assets, **kw)
        return xr.Dataset()

    it = Items([item_with("a", D(2020), d=(("x.cap"), "application/x-cap"))])
    w = it.open_settings()
    assert w.opener == "cap" and w.kwargs() == {"n": 3}
    it.open(w, extra=1, n=5)
    assert seen == {"assets": ["d"], "n": 5, "extra": 1}
    seen.clear()
    it.open("d")
    assert seen == {"assets": ["d"]}


def test_collection_open_settings():
    col = make_collection("c")
    col.add_asset("cube", pystac.Asset("c.zarr", media_type="application/vnd+zarr", roles=["data"]))
    w = stac_viewer.CollectionView(col, None).open_settings()
    assert w.assets == ["cube"] and w.opener == "zarr"
    empty = stac_viewer.CollectionView(make_collection("e"), None).open_settings()
    assert empty.assets == [] and "no openable" in empty.error


def test_open_esm_syntax(tmp_path):
    import shutil, subprocess

    if not shutil.which("node"):
        pytest.skip("node not installed")
    from stac_viewer import core

    for n, src in {"open": core._OPEN_ESM, "sel": core._ESM}.items():
        f = tmp_path / f"{n}.mjs"
        f.write_text(src)
        subprocess.run(["node", "--check", str(f)], check=True)

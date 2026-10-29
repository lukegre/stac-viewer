import datetime as dt
import json

import pystac
import pystac_client
import pytest

from stac_viewer import Items, browse
from stac_viewer import core
from stac_viewer.core import _bar_span, _dt_range, _ident

UTC = dt.timezone.utc


def D(y, m=1, d=1):
    return dt.datetime(y, m, d, tzinfo=UTC)


def make_item(id, bbox, when=None, start=None, end=None, **props):
    w, s, e, n = bbox
    geom = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    if start:
        props.update(start_datetime=start.isoformat(), end_datetime=end.isoformat())
    return pystac.Item(id, geom, bbox, when, props)


def make_collection(id, items=(), bbox=(-10, -10, 10, 10), t0=D(2000), t1=None):
    ext = pystac.Extent(pystac.SpatialExtent([list(bbox)]), pystac.TemporalExtent([[t0, t1]]))
    col = pystac.Collection(id, f"desc {id}", ext, title=id.title())
    col.add_items(list(items))
    return col


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    d = tmp_path / "cache"
    monkeypatch.setenv("STAC_VIEWER_CACHE", str(d))
    return d


@pytest.fixture
def catalog():
    cat = pystac.Catalog("root", "root")
    a = make_collection(
        "alpha",
        [
            make_item("a1", (0, 0, 5, 5), D(2020, 1, 15), **{"eo:cloud_cover": 12.0}),
            make_item("a2", (6, 6, 9, 9), D(2021, 6, 1)),
            make_item("a3", (0, 0, 5, 5), None, start=D(2019, 12, 1), end=D(2020, 2, 1)),
        ],
    )
    # items nested in a sub-catalog inside the collection
    sub = pystac.Catalog("sub", "sub")
    sub.add_item(make_item("deep", (1, 1, 2, 2), D(2020, 3, 3)))
    a.add_child(sub)
    cat.add_child(a)
    # collection nested in a sub-catalog
    group = pystac.Catalog("group", "group")
    group.add_child(make_collection("nested-one", [make_item("n1", (0, 0, 1, 1), D(2022, 1, 1))]))
    cat.add_child(group)
    return cat


@pytest.fixture
def cols(catalog):
    return browse(catalog)


# ---------------------------------------------------------------- collections / idents
def test_nested_collections_found(cols):
    assert len(cols) == 2
    assert cols.nested_one.id == "nested-one"
    assert cols["alpha"].id == "alpha"


def test_ident():
    taken = set()
    assert _ident("a-b", taken) == "a_b"
    assert _ident("1abc", taken) == "_1abc"
    assert _ident("class", taken) == "class_"
    assert _ident("", taken) == "_"
    assert _ident("a-b", {"a_b"}) == "a_b_2"
    assert _ident("a b", {"a_b", "a_b_2"}) == "a_b_3"


# ---------------------------------------------------------------- local search
def ids(items):
    return sorted(i.id for i in items)


def test_returns_items_and_recursive(cols):
    res = cols.alpha.search()
    assert isinstance(res, Items)
    assert ids(res) == ["a1", "a2", "a3", "deep"]


def test_bbox_filter(cols):
    assert ids(cols.alpha.search(bbox=[0, 0, 3, 3])) == ["a1", "a3", "deep"]
    assert ids(cols.alpha.search(bbox=[8, 8, 20, 20])) == ["a2"]


def test_datetime_string(cols):
    assert ids(cols.alpha.search(datetime="2021-01-01/2021-12-31")) == ["a2"]
    assert ids(cols.alpha.search(datetime="2020-01-15")) == ["a1", "a3"]  # a3 range covers the day


def test_datetime_open_ended(cols):
    assert ids(cols.alpha.search(datetime="2021-01-01/..")) == ["a2"]
    assert ids(cols.alpha.search(datetime="../2020-01-01")) == ["a3"]
    assert ids(cols.alpha.search(datetime="2021-01-01/")) == ["a2"]


def test_datetime_objects(cols):
    assert ids(cols.alpha.search(datetime=D(2021, 6, 1))) == ["a2"]
    assert ids(cols.alpha.search(datetime=dt.date(2021, 6, 1))) == ["a2"]
    assert ids(cols.alpha.search(datetime=dt.datetime(2021, 6, 1))) == ["a2"]  # naive -> UTC
    assert ids(cols.alpha.search(datetime=(dt.date(2021, 1, 1), None))) == ["a2"]
    assert ids(cols.alpha.search(datetime=[D(2020, 3, 1), "2020-03-31"])) == ["deep"]


def test_dt_range_normalisation():
    assert _dt_range(None) == (None, None)
    s, e = _dt_range("2020-01-01/2020-01-31")
    assert s == D(2020, 1, 1) and e.date() == dt.date(2020, 1, 31) and e.hour == 23
    assert _dt_range("..") == (None, None)
    s, e = _dt_range("2020-05-05T10:00:00Z")
    assert s == e == dt.datetime(2020, 5, 5, 10, tzinfo=UTC)
    assert _dt_range((None, "..")) == (None, None)


def test_ids_filter(cols):
    assert ids(cols.alpha.search(ids=["a2", "deep"])) == ["a2", "deep"]
    assert len(cols.alpha.search(ids=["nope"])) == 0


def test_unsupported_kwarg(cols):
    with pytest.raises(TypeError, match="foo.*bbox, intersects, datetime, ids, query, max_items"):
        cols.alpha.search(foo=1)


def test_max_items(cols):
    res = cols.alpha.search(max_items=2)
    assert len(res) == 2 and res._limit == 2
    assert len(cols.alpha.search(max_items=None)) == 4


def test_search_with_selector(cols):
    col = cols.alpha
    sel = col.selector()
    sel.bbox = [6.0, 6.0, 9.0, 9.0]
    assert ids(col.search(sel)) == ["a2"]
    assert ids(col.search(sel, bbox=[0, 0, 3, 3])) == ["a1", "a3", "deep"]  # explicit wins
    assert len(col.search()) == 4  # selector not remembered
    sel.bbox = [-10.0, -10.0, 10.0, 10.0]
    sel.start, sel.end = "2021-01-01", "2021-12-31"
    assert ids(col.search(sel)) == ["a2"]
    assert ids(col.search(sel, datetime="2020-01-15")) == ["a1", "a3"]


def test_selector_dates(cols):
    sel = cols.alpha.selector()
    assert sel.min_date == "2000-01-01" and sel.max_date == dt.datetime.now(UTC).strftime("%Y-%m-%d")


def test_attribution_reduced():
    assert core._TILE_ATTR == "&copy; Esri, OSM"
    assert "setPrefix(false)" in core._LEAFLET_JS and "stacmap-style" in core._LEAFLET_JS


# ---------------------------------------------------------------- rendering
def test_items_repr(cols):
    res = cols.alpha.search()
    h = res._repr_html_()
    assert "<svg" in h and "4 items" in h and "12% cloud" in h
    assert "truncated" not in h
    assert "2020-01-15 00:00:00 UTC" in h and "2019-12-01 00:00:00 UTC to 2020-02-01 00:00:00 UTC" in h
    assert repr(res) == "<Items n=4>"
    assert "truncated" in cols.alpha.search(max_items=2)._repr_html_()


def test_items_repr_empty(cols):
    assert "0 items" in cols.alpha.search(ids=["x"])._repr_html_()


def test_collection_html_has_land_and_bar(cols):
    h = cols.alpha._repr_html_()
    assert "<svg" in h and "<path" in h and "class='bar'" in h


def test_bar_span():
    now = D(2020)
    y0, left, width = _bar_span(D(2000), D(2010), now=now)
    assert y0 == 1980
    assert left == pytest.approx(50, abs=1) and width == pytest.approx(25, abs=1)
    y0, left, width = _bar_span(None, None, now=now)  # open both ends -> full bar
    assert (left, width) == (0, 100)
    y0, left, width = _bar_span(D(1950), None, now=now)  # earlier than 1980 extends axis
    assert y0 == 1950 and left == 0 and width == 100


# ---------------------------------------------------------------- static catalogs via Client (bug e)
@pytest.fixture
def saved(catalog, tmp_path):
    catalog.normalize_hrefs(str(tmp_path))
    catalog.save(pystac.CatalogType.SELF_CONTAINED)
    return str(tmp_path / "catalog.json")


def test_browse_json_path(saved):
    cols = browse(saved)
    assert len(cols) == 2
    assert ids(cols.alpha.search()) == ["a1", "a2", "a3", "deep"]


def test_static_catalog_via_client_falls_back_to_local(saved):
    client = pystac_client.Client.open(saved)
    cols = browse(client)
    res = cols.alpha.search(bbox=[8, 8, 20, 20])
    assert ids(res) == ["a2"]


# ---------------------------------------------------------------- browse() URL handling
@pytest.mark.parametrize("url", [
    "https://x.org/api/stac/v1",
    "https://x.org/api/stac/v1/",
    "https://x.org/api/stac/v1/collections",
    "https://x.org/api/stac/v1/collections/",
])
def test_browse_url_stripping(monkeypatch, catalog, url):
    seen = {}

    def fake_open(u, **kw):
        seen.update(url=u, kw=kw)
        return catalog

    monkeypatch.setattr(pystac_client.Client, "open", fake_open)
    browse(url, modifier="m")
    assert seen == {"url": "https://x.org/api/stac/v1", "kw": {"modifier": "m"}}


def test_svg_map_default_world_unchanged():
    svg = core._svg_map([[0, 0, 5, 5]])
    assert '<rect x="120.0" y="56.7" width="3.3" height="3.3"' in svg
    assert 'transform="scale(0.6667, 0.6667)"' in svg and "overflow" not in svg
    assert svg.count("<line") == 7 + 7 and '<line x1="0.0" y1="0" x2="0.0" y2="120"/>' in svg


def test_svg_map_zoomed_view():
    svg = core._svg_map([[8, 47, 9, 47.5]], view=(7, 46.5, 10, 48))
    assert '<rect x="80.0" y="40.0" width="80.0" height="40.0"' in svg  # (8-7)/3*240, (48-47.5)/1.5*120
    assert 'transform="translate(-14960.0000, -3360.0000) scale(80.0000, 80.0000)"' in svg
    assert "overflow:hidden" in svg and svg.count("<line") == 8


def test_items_view_aspect_and_clamp():
    w, s, e, n = core._items_view([[8, 47, 9, 47.5]])
    assert (e - w) == pytest.approx(2 * (n - s)) and w < 8 and e > 9 and s < 47 and n > 47.5
    assert core._items_view([[-180, -90, 180, 90]]) == (-180, -90, 180, 90)
    assert core._items_view([[170, 0, -170, 1]]) is None


# ---------------------------------------------------------------- description + leaflet maps
def test_short_description_no_details():
    col = core.CollectionView(make_collection("short"), None)
    h = col._repr_html_()
    assert "desc short" in h and "class='dsc" not in h


def test_long_description_expandable():
    col = make_collection("long")
    col.description = ("word " * 100).strip() + " <b>end</b>"
    h = core.CollectionView(col, None)._repr_html_()
    assert "<details class='dsc" in h and "&hellip; more" in h and "show less" in h
    assert "&lt;b&gt;end&lt;/b&gt;" in h and "<b>end</b>" not in h  # full text, escaped
    head = h.split("<span class='dmore'>")[1].split("&hellip;")[0]
    assert len(head) <= 300 and head.endswith("word")  # cut at a word boundary


def test_collection_card_map(cols):
    h = cols.alpha._repr_html_()
    assert 'id="stacmap-' in h and "<svg" in h  # fallback inside the container
    assert "leaflet@1.9.4" in h and "<script>" in h and "type=\"module\"" not in h


def test_script_cannot_be_broken_out_of():
    col = make_collection("x</script>y")
    col.description = "</script><script>alert(1)</script>"
    h = core.CollectionView(col, None)._repr_html_()
    assert h.count("</script>") == 1  # only our own closing tag
    items = Items([make_item("i</script>d", (0, 0, 1, 1), D(2020))])
    assert items._repr_html_().count("</script>") == 1


def test_items_card_embeds_geometries(cols):
    h = cols.alpha.search()._repr_html_()
    assert 'id="stacmap-' in h and "leaflet@1.9.4" in h
    assert '"type": "Polygon"' in h and "a1 \\u00b7 2020-01-15" in h


def test_widget_has_no_land_trait(cols):
    sel = cols.alpha.selector()
    assert not sel.has_trait("land") and "leaflet" in sel._esm

# ---------------------------------------------------------------- collection cache
@pytest.fixture
def counted(monkeypatch):
    calls = []
    real = core._fetch_collections

    def fetch(catalog):
        calls.append(1)
        return real(catalog)

    monkeypatch.setattr(core, "_fetch_collections", fetch)
    return calls


def test_cache_hit_and_search_from_cache(saved, counted, cache_dir):
    browse(saved)
    cols = browse(saved)
    assert len(counted) == 1 and len(list(cache_dir.glob("*.json"))) == 1
    assert len(cols) == 2 and cols.nested_one.title == "Nested-One"
    # relative links of the static catalog still resolve from the cached collections
    assert ids(cols.alpha.search()) == ["a1", "a2", "a3", "deep"]


def test_cache_refresh_flag(saved, counted):
    browse(saved)
    browse(saved, refresh=True)
    assert len(counted) == 2


def test_cache_expires(saved, counted, cache_dir):
    import os, time

    browse(saved)
    (path,) = cache_dir.glob("*.json")
    old = time.time() - core.CACHE_MAX_AGE.total_seconds() - 60
    os.utime(path, (old, old))
    browse(saved)
    assert len(counted) == 2
    assert path.stat().st_mtime > old  # rewritten


def test_corrupt_cache_refetches(saved, counted, cache_dir):
    browse(saved)
    (path,) = cache_dir.glob("*.json")
    path.write_text("{not json")
    assert len(browse(saved)) == 2
    assert len(counted) == 2


def test_objects_not_cached(catalog, cache_dir):
    browse(catalog)
    assert not cache_dir.exists()

# ---------------------------------------------------------------- lazy /collections parsing
def test_api_collections_parsed_lazily(saved, monkeypatch):
    seen = []
    client = pystac_client.Client.open(saved, modifier=lambda obj: seen.append(obj.id))
    pages = [{"collections": [make_collection("alpha").to_dict()]},
             {"collections": [make_collection("beta-two").to_dict()]}]
    monkeypatch.setattr(client, "_supports_collections", lambda: True)
    monkeypatch.setattr(client, "_collections_href", lambda: "https://x.org/collections")
    monkeypatch.setattr(client._stac_io, "get_pages", lambda url, **kw: iter(pages))
    built = []
    orig = core.CollectionClient.from_dict
    monkeypatch.setattr(core.CollectionClient, "from_dict",
                        lambda d, **kw: built.append(d["id"]) or orig(d, **kw))

    cols = browse(client)
    assert list(cols._by_attr) == ["alpha", "beta_two"]
    assert "Beta-Two" in cols._repr_html_() and repr(cols.beta_two) == "<Collection beta-two>"
    assert built == [] and seen == []  # listing, repr and overview parse nothing

    assert cols.beta_two.description == "desc beta-two"
    assert built == ["beta-two"] and seen == ["beta-two"]
    cols.beta_two._repr_html_()
    assert built == ["beta-two"]  # parsed once, then cached


@pytest.mark.parametrize("rejects_limit", [False, True])
def test_collection_pages_ask_for_large_pages(saved, monkeypatch, rejects_limit):
    client = pystac_client.Client.open(saved)
    monkeypatch.setattr(client, "_collections_href", lambda: "https://x.org/collections")
    calls = []

    def get_pages(url, parameters=None):
        calls.append(parameters)
        if parameters and rejects_limit:
            raise pystac_client.exceptions.APIError("limit too large")
        yield {"collections": [{"id": "a"}]}
        yield {"collections": [{"id": "b"}]}

    monkeypatch.setattr(client._stac_io, "get_pages", get_pages)
    pages = list(core._collection_pages(client))
    assert [c["id"] for p in pages for c in p["collections"]] == ["a", "b"]
    assert calls == ([{"limit": core._PAGE_LIMIT}, None] if rejects_limit else [{"limit": core._PAGE_LIMIT}])


def test_api_collections_cached_raw(saved, monkeypatch, cache_dir):
    client = pystac_client.Client.open(saved)
    monkeypatch.setattr(client, "_supports_collections", lambda: True)
    monkeypatch.setattr(client, "_collections_href", lambda: "https://x.org/collections")
    monkeypatch.setattr(client._stac_io, "get_pages",
                        lambda url, **kw: iter([{"collections": [make_collection("alpha").to_dict()]}]))
    core._cached_collections("api", client)
    cols = core.Collections(client, core._cached_collections("api", client))
    assert isinstance(cols.alpha._raw, dict)  # read back unparsed from the cache
    assert cols.alpha.description == "desc alpha"


def _script(h):
    return h.split("<script>", 1)[1].rsplit("</script>", 1)[0]


def test_items_card_is_item_browser():
    a, b, c = (make_item(x, (0, 0, 1, 1), D(2020), **{"eo:cloud_cover": 5.0}) for x in "abc")
    a.add_asset("t", pystac.Asset("https://x.org/a.png", media_type="image/png", roles=["thumbnail"]))
    b.add_asset("thumbnail", pystac.Asset("s3://bucket/b.png", media_type="image/png"))
    c.add_asset("rendered_preview", pystac.Asset("https://x.org/c.tif", media_type="image/tiff"))
    h = Items([a, b, c])._repr_html_()
    assert all(f"data-i='{i}'" in h for i in range(3)) and "data-i='3'" not in h
    feats = json.loads(_script(h).split("data=", 1)[1].split(";", 1)[0])["features"]
    assert [f["properties"].get("thumb") for f in feats] == ["https://x.org/a.png", None, None]
    assert feats[0]["properties"]["props"]["cloud cover"] == "5%" and "<details" not in h


def test_items_card_item_data_cannot_break_out():
    it = make_item("<img src=x onerror=alert(1)></script>", (0, 0, 1, 1), D(2020), platform="</script><script>x")
    it.add_asset("thumbnail", pystac.Asset("javascript:alert(1)", media_type="image/png"))
    h = Items([it])._repr_html_()
    assert h.count("</script>") == 1 and "<img src=x" not in h.replace(_script(h), "") and "javascript:" not in h


def test_collection_card_still_renders_map():
    h = core.CollectionView(make_collection("x"), None)._repr_html_()
    assert 'id="stacmap-' in h and "<tr data-i" not in h

# ---------------------------------------------------------------- polygon / query options
class FakeClient(pystac_client.Client):
    """Records pystac_client search kwargs; pretends item-search is available."""

    def __init__(self):
        self.calls = []

    def conforms_to(self, c):
        return True

    def search(self, **kw):
        self.calls.append(kw)
        return type("S", (), {"items": lambda s: iter([])})()


@pytest.fixture
def api(cols, monkeypatch):
    monkeypatch.setattr(core, "Client", FakeClient)
    view = core.CollectionView(cols.alpha._col, FakeClient())
    return view, view._catalog.calls


def test_polygon_search_uses_intersects(cols, api):
    view, calls = api
    sel = cols.alpha.selector()
    sel.polygon = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0]]
    sel.bbox = [0.0, 0.0, 4.0, 4.0]
    view.search(sel)
    assert calls[0]["bbox"] is None
    assert calls[0]["intersects"] == {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 0]]]}
    view.search(sel, bbox=[1, 1, 2, 2])  # explicit bbox wins, no intersects
    assert calls[1]["bbox"] == [1, 1, 2, 2] and calls[1]["intersects"] is None
    view.search(intersects={"type": "Point", "coordinates": [1, 1]})
    assert calls[2]["intersects"]["type"] == "Point"


def test_polygon_static_filters_by_bounds(cols):
    sel = cols.alpha.selector()
    sel.polygon = [[6.0, 6.0], [9.0, 6.0], [9.0, 9.0]]
    assert ids(cols.alpha.search(sel)) == ["a2"]
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [3, 0], [3, 3], [0, 0]]]}
    assert ids(cols.alpha.search(intersects=geom)) == ["a1", "a3", "deep"]


def test_selector_query(cols, api):
    view, calls = api
    sel = cols.alpha.selector()
    sel.cloud_cover, sel.filters = 20, {"platform": "s2a"}
    view.search(sel)
    assert calls[0]["query"] == {"platform": {"eq": "s2a"}, "eo:cloud_cover": {"lte": 20}}
    view.search(sel, query={"eo:cloud_cover": {"lte": 5}})  # explicit wins per key
    assert calls[1]["query"] == {"platform": {"eq": "s2a"}, "eo:cloud_cover": {"lte": 5}}
    view.search()
    assert calls[2]["query"] is None


def test_selector_query_static(cols):
    sel = cols.alpha.selector()
    sel.cloud_cover = 50
    assert ids(cols.alpha.search(sel)) == ["a1"]  # items without the property are excluded


def test_local_query_ops(cols):
    q = lambda **kw: ids(cols.alpha.search(query={"eo:cloud_cover": kw}))
    assert q(lt=12.0) == [] and q(lte=12.0) == ["a1"] and q(gt=12.0) == [] and q(gte=12.0) == ["a1"]
    assert q(eq=12.0) == ["a1"] and q(neq=12.0) == []  # missing property never matches
    assert q(gte=1, lt=50) == ["a1"]


def test_selector_summaries(catalog):
    col = catalog.get_child("alpha")
    col.summaries = pystac.Summaries({"platform": ["b", "a"], "constellation": ["x"], "sat:orbit_state": [1, 2], "eo:bands": [{}]})
    sel = browse(catalog).alpha.selector()
    assert sel.has_cloud and sel.filter_options == {"platform": ["a", "b"]}
    col.summaries = pystac.Summaries({})
    col.stac_extensions = ["https://stac-extensions.github.io/eo/v1.1.0/schema.json"]
    assert browse(catalog).alpha.selector().has_cloud
    col.stac_extensions = []
    assert not browse(catalog).alpha.selector().has_cloud

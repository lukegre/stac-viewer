import datetime as dt

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
    with pytest.raises(TypeError, match="foo.*bbox, datetime, ids, max_items"):
        cols.alpha.search(foo=1)


def test_max_items(cols):
    res = cols.alpha.search(max_items=2)
    assert len(res) == 2 and res._limit == 2
    assert len(cols.alpha.search(max_items=None)) == 4


def test_clear_selection(cols):
    col = cols.alpha
    sel = col.selector()
    sel.bbox = [6.0, 6.0, 9.0, 9.0]
    assert ids(col.search()) == ["a2"]
    assert ids(col.search(bbox=[0, 0, 3, 3])) == ["a1", "a3", "deep"]  # explicit wins
    col.clear_selection()
    assert len(col.search()) == 4


# ---------------------------------------------------------------- rendering
def test_items_repr(cols):
    res = cols.alpha.search()
    h = res._repr_html_()
    assert "<svg" in h and "4 items" in h and "12% cloud" in h
    assert "truncated" not in h
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

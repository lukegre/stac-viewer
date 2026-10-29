# stac-viewer

Browse STAC catalogs in Jupyter with dot notation, HTML viewers, an interactive bbox/date selector and search.

## Install

```bash
pip install stac-viewer             # core
pip install "stac-viewer[widget]"   # + interactive selector() (anywidget)
```

## Usage

```python
from stac_viewer import browse

cat = browse("https://earth-search.aws.element84.com/v1")  # API URL, catalog.json path/URL, or pystac object
cat                                                         # table of collections
col = cat.sentinel_2_l2a                                    # HTML card: extent map, temporal bar, metadata
col.selector()                                              # draw bbox + pick dates (needs the widget extra)
items = col.search()                                        # uses the selection -> Items (HTML summary)
items = col.search(bbox=[8.4, 47.3, 8.7, 47.5], datetime="2026-06-01/2026-06-30", max_items=50)
col.clear_selection()                                       # search() stops using the selector
```

Extra keyword arguments to `browse` go to `pystac_client.Client.open`, e.g.
`browse(url, modifier=planetary_computer.sign_inplace)`.

## APIs vs static catalogs

- **STAC APIs** (with item-search): server-side search; all `pystac_client` search kwargs
  (`query`, `filter`, `sortby`, ...) are passed through.
- **Static catalogs** (`catalog.json`): collections are found recursively and items are filtered
  client-side. Only `bbox`, `datetime`, `ids` and `max_items` are supported; anything else raises `TypeError`.

`datetime` accepts `"a/b"`, `"a"`, open ends (`"../b"`), `datetime`/`date` objects, or a `(start, end)` tuple.
`search()` returns `max_items=1000` items by default; pass `max_items=None` for no limit.

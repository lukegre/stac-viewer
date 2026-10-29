import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium", css_file="full_height.css")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Browsing the Planetary Computer with `stac_viewer`

    `stac_viewer` turns any STAC catalog or API into something you can explore in a notebook: collections become attributes (`cat.sentinel_2_l2a`), each collection renders as a small card (footprint map, temporal extent, metadata), and `search()` returns items with a summary map. A bbox + date selector widget lets you pick a search region interactively.

    This notebook uses Microsoft's Planetary Computer STAC API. Work through it step by step: pick a collection, choose a region and dates, then press the buttons to search, open and compute. Nothing heavy runs until you click, and imagery is only read when you press the mosaic button at the end.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Open the catalog

    `browse()` accepts an API URL, a `catalog.json` path/URL or a pystac object. A trailing `/collections/` is stripped automatically, and extra keyword arguments go to `pystac_client.Client.open`. Here the `modifier` signs asset hrefs so they can be read later.
    """)
    return


@app.cell
def _():
    import planetary_computer
    import rioxarray

    from stac_viewer import browse

    return browse, planetary_computer


@app.cell
def _(browse, planetary_computer):
    cat = browse(
        "https://planetarycomputer.microsoft.com/api/stac/v1/collections/",
        modifier=planetary_computer.sign_inplace,
    )
    cat
    return (cat,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Pick a collection

    Collections are available by dot access (ids are turned into valid Python identifiers) or by bracket access with the original id. Click the copy icon next to a collection in the table above and paste its name into the field below.
    """)
    return


@app.cell
def _(mo):
    collection_id = mo.ui.text(value="sentinel_2_l2a", label="Collection", full_width=True)
    collection_id
    return (collection_id,)


@app.cell
def _(cat, collection_id, mo):
    _name = collection_id.value.strip().removeprefix("cat.").strip()
    collection = getattr(cat, _name, None)
    mo.stop(
        collection is None,
        mo.callout(
            mo.md(f"Collection `{_name}` not found. Use the copy icon in the catalog table above and paste the name here."),
            kind="warn",
        ),
    )
    collection
    return (collection,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Interactive selector

    The card above shows the spatial footprint, temporal extent, license, summaries and item assets. `selector()` shows a map with a draggable bounding box plus start/end date fields. `search()` then uses the selection whenever you do not pass `bbox`/`datetime` explicitly. Wrapping the widget in `mo.ui.anywidget` makes marimo rerun the search cell whenever you change the selection.
    """)
    return


@app.cell
def _(collection, mo):
    _sel = collection.selector()
    _sel.bbox = [8.41, 47.30, 8.71, 47.49]  # preset bounding box
    sel = mo.ui.anywidget(_sel)
    sel
    return (sel,)


@app.cell
def _(mo):
    search_btn = mo.ui.run_button(label="Search items")
    search_btn
    return (search_btn,)


@app.cell
def _(collection, mo, search_btn, sel):
    mo.stop(not search_btn.value, mo.md("Choose a region and dates, then press **Search items**."))

    with mo.status.spinner("Searching items..."):
        items = collection.search(sel.widget, max_items=20)
    items
    return (items,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Open the items

    `open_settings()` picks assets and opener settings. For Sentinel-2 L2A, defaults are set in code (RGB bands, 25 m) for reproducible results; changing them in the widget reruns the cells below.
    """)
    return


@app.cell
def _(collection, items, mo):
    _settings = items.open_settings()
    if collection._id == "sentinel-2-l2a":
        _settings.set(assets=["B04", "B03", "B02"], resolution=25)
    settings = mo.ui.anywidget(_settings)
    settings
    return (settings,)


@app.cell
def _(mo):
    open_btn = mo.ui.run_button(label="Open dataset")
    open_btn
    return (open_btn,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Open as `xarray.Dataset`
    Open the STAC items as an `xarray.Dataset`. Settings to open the data are taken from the settings widget defined above.
    """)
    return


@app.cell
def _(items, mo, open_btn, settings):
    mo.stop(not open_btn.value, mo.md("Press **Open dataset** to open the items lazily."))

    out = items.open(settings.widget, chunks={})#.rio.clip_box(*sel.value['bbox'], crs="epsg:4326")
    out
    return (out,)


@app.cell
def _(out):
    a = out.tasmax[-365:]
    return (a,)


@app.cell
def _(a):
    a
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Median mosaic

    So far everything is lazy: `out` is a dask-backed dataset and no pixels have been read. The button below triggers the read and computes a per-pixel median over time, which removes most clouds. Three-band datasets are shown as RGB (reflectance scaled by 15000, no-data masked); otherwise the first variable is plotted.
    """)
    return


@app.cell
def _(mo):
    run = mo.ui.run_button(label="Compute median mosaic")
    run
    return (run,)


@app.cell
def _(mo, out, run):
    mo.stop(not run.value, mo.md("Press the button to read the imagery and compute the mosaic."))

    with mo.status.spinner("Computing median mosaic..."):
        mosaic = out.median("time").compute()
    return (mosaic,)


@app.cell
def _(mosaic):
    if len(mosaic.data_vars) == 3:
        _img = (
            mosaic
            .to_array(dim="band")
            .pipe(lambda x: x / 10_000)
            .pipe(lambda x: x[::-1])
            .plot.imshow(size=5, vmin=0.0, vmax=0.15)
        )
    else:
        _img = mosaic[list(mosaic.data_vars)[0]].plot.imshow(size=5, robust=True)

    _img.axes.set_aspect("auto")
    _img.axes
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

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

    This notebook uses Microsoft's Planetary Computer STAC API. Imagery is only read when you press the mosaic button at the end.
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
    ## Look at a collection

    Collections are available by dot access (ids are turned into valid Python identifiers) or by bracket access with the original id. The card shows the spatial footprint, temporal extent, license, summaries and item assets.
    """)
    return


@app.cell
def _(cat):
    cat.sentinel_2_l2a
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Interactive selector

    `selector()` shows a map with a draggable bounding box plus start/end date fields. `search()` then uses the selection whenever you do not pass `bbox`/`datetime` explicitly. Wrapping the widget in `mo.ui.anywidget` makes marimo rerun the search cell whenever you change the selection.
    """)
    return


@app.cell
def _(cat, mo):
    _sel = cat.sentinel_2_l2a.selector()
    _sel.bbox = [8.41, 47.30, 8.71, 47.49]  # preset bounding box
    sel = mo.ui.anywidget(_sel)
    sel
    return (sel,)


@app.cell
def _(cat, sel):
    items = cat.sentinel_2_l2a.search(sel.widget, max_items=20)
    items
    return (items,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Open the items

    `open_settings()` picks assets and opener settings. Defaults are set in code for reproducible results; changing them in the widget reruns the cells below.
    """)
    return


@app.cell
def _(items, mo):
    _settings = items.open_settings()
    _settings.set(assets=["B04", "B03", "B02"], resolution=25)
    settings = mo.ui.anywidget(_settings)
    settings
    return (settings,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Open as `xarray.Dataset`
    Open the STAC items as an `xarray.Dataset`. Settings to open the data are taken from the settings widget defined above
    """)
    return


@app.cell
def _(items, settings):
    out = items.open(settings.widget)
    out
    return (out,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Median mosaic

    So far everything is lazy: `out` is a dask-backed dataset and no pixels have been read. The button below triggers the read and computes a per-pixel median over time, which removes most clouds. The RGB plot scales reflectance by 15000 and masks no-data pixels.
    """)
    return


@app.cell
def _(mo):
    run =mo.ui.run_button(label="Compute median mosaic")
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
    _img = (
        mosaic
        .to_array(dim="band")
        .pipe(lambda x: x / 15000)
        .where(lambda x: x > 0.01)
        .plot.imshow(size=5, vmin=0.03, vmax=0.2)
    )
    _img.axes
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

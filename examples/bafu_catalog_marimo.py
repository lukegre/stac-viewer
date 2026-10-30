import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium", css_file="full_height.css")


@app.cell(hide_code=True)
def _():
    import marimo as mo
    from xyzservices import providers as xyz

    from stac_viewer import browse

    return browse, mo, xyz


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Browsing the Swiss federal geodata catalog with `stac_viewer`

    The Swiss Federal Geoportal (data.geo.admin.ch) publishes its datasets, including those from the Federal Office for the Environment (BAFU), through a public STAC API. No signing or authentication is needed, so assets can be read directly.

    Click on the entries below to load a STAC collection. Note that not all formats are supported at the moment
    """)
    return


@app.cell(hide_code=True)
def _(browse, mo):
    bafu = browse("https://data.geo.admin.ch/api/stac/v1/")
    picker = mo.ui.anywidget(bafu.picker("ch.bafu.trockenheitswarnkarte"))
    picker
    return (picker,)


@app.cell
def _(picker):
    collection = picker.widget.collection
    collection
    return (collection,)


@app.cell
def _(collection):
    sel = collection.selector()
    sel
    return (sel,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Search for items

    Without a `bbox` or `datetime`, `search()` returns all items in the collection. Here that is a single item covering Switzerland.
    """)
    return


@app.cell
def _(collection, sel):
    items = collection.search(sel)
    items
    return (items,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Open the items

    `open_settings()` picks assets and opener settings. The resolution is set in code (in the units of the data's CRS, here metres in Swiss LV95 / EPSG:2056); changing it in the widget reruns the cells below.
    """)
    return


@app.cell
def _(items, mo):
    _settings = items.open_settings()
    settings = mo.ui.anywidget(_settings)
    settings
    return (settings,)


@app.cell
def _(items, settings):
    out = items.open(settings.widget)
    out
    return (out,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Plot

    Each asset becomes a variable in the dataset, named after its file. Plotting reads the data, so this is the step that touches the actual GeoTIFF.
    """)
    return


@app.cell
def _(out):
    out["fokuskarte-voegel_2056.tif"].squeeze().plot.imshow()
    return


@app.cell
def _(mo, out):
    _cols = [c for c in out.columns if c != out.geometry.name]
    column = mo.ui.dropdown(_cols, value=_cols[0] if _cols else None, label="column")
    column
    return (column,)


@app.cell
def _(column, out, xyz):
    out.explore(
        column=column.value,
        tiles=xyz.SwissFederalGeoportal.NationalMapGrey,
        cmap="Spectral_r",
    )
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium", css_file="full_height.css")


@app.cell
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

    This notebook opens the catalog, picks a forest climate layer and loads it as an `xarray` dataset.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Open the catalog

    `browse()` takes the API URL; the trailing `/collections` is stripped automatically. The catalog view lists all collections and is scrollable. `picker()` shows the same view as a widget: clicking a row makes that collection the active one for the cells below.
    """)
    return


@app.cell(hide_code=True)
def _(browse, mo):
    bafu = browse("https://data.geo.admin.ch/api/stac/v1/")
    picker = mo.ui.anywidget(bafu.picker("ch.bafu.trockenheitswarnkarte"))
    picker
    return (picker,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Look at a collection

    Collection ids like `ch.bafu.wald-lufttemperatur_juli_1981_2010` contain dots and dashes, so they are turned into valid Python identifiers for dot access. Bracket access with the original id works too. This collection holds the mean July air temperature in Swiss forests for 1981–2010.
    """)
    return


@app.cell
def _(picker):
    collection = picker.widget.collection
    collection
    return (collection,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Search for items

    Without a `bbox` or `datetime`, `search()` returns all items in the collection. Here that is a single item covering Switzerland.
    """)
    return


@app.cell
def _(collection):
    items = collection.search()
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
def _(out, xyz):
    out.explore(
        tiles=xyz.SwissFederalGeoportal.NationalMapGrey,
        cmap="YlOrRd", 
        column="warnlevel"
    )
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

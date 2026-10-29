import marimo

__generated_with = "0.25.2"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    from stac_viewer import browse

    return browse, mo


@app.cell
def _(browse):
    bafu = browse("https://data.geo.admin.ch/api/stac/v1/collections")
    bafu
    return (bafu,)


@app.cell
def _(bafu):
    collection = bafu.ch_bafu_wald_lufttemperatur_juli_1981_2010
    collection
    return (collection,)


@app.cell
def _(collection):
    items = collection.search()
    items
    return (items,)


@app.cell
def _(items, mo):
    _settings = items.open_settings()
    _settings.set(resolution=100)
    settings = mo.ui.anywidget(_settings)
    settings
    return (settings,)


@app.cell
def _(items, settings):
    out = items.open(settings.widget)
    out
    return (out,)


@app.cell
def _(out):
    _img = (
        out["wald-lufttemperatur_juli_1981_2010_2056.tif"]
        .squeeze()
        .plot.imshow(aspect=2, size=6, cmap="Spectral_r", vmin=0, vmax=25)
    )
    _img.axes
    return


if __name__ == "__main__":
    app.run()

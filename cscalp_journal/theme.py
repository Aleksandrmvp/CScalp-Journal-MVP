"""Photo backdrop for the dashboard pages (dark theme only).

A dark veil keeps text readable and the cards turn into translucent frosted panels so the
desk photo stays visible behind them. Live pages serve the image from /static/bg.jpg; the
public snapshot embeds it as a data: URI.
"""


from pathlib import Path

BG_PATH = Path(__file__).parent / "static" / "bg.jpg"   # optional: drop your own photo here


def photo_rules(prefix: str, photo: str) -> str:
    return (
        f"{prefix} body{{background:linear-gradient(rgba(8,9,12,.22),rgba(8,9,12,.50)),"
        f"{photo} center/cover fixed no-repeat,#0b0c0f}}\n"
        f"{prefix} header{{background:rgba(8,9,12,.55);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)}}\n"
        f"{prefix} .card,{prefix} .bundle{{background:rgba(12,14,19,.60);"
        f"backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}}\n"
    )


BG_STYLE = '<style id="photo-bg">\n' + photo_rules(':root[data-theme="dark"]', "url(/static/bg.jpg)") + "</style>"


def with_photo(page: str) -> str:
    if not BG_PATH.exists():
        return page
    return page.replace("</head>", BG_STYLE + "</head>", 1)

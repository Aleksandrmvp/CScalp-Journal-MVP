"""Static, read-only snapshot of the /report page for public sharing.

Embeds the report data for every month into one self-contained HTML fragment (no API, no
write endpoints, no settings form), themed for the viewer's light/dark mode.

Usage:
    python -m cscalp_journal.snapshot [output.html]   # default: data/report_snapshot.html
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path

from . import db, report
from .report_page import REPORT_PAGE
from .theme import BG_PATH, photo_rules

_LIGHT = ("--bg:#f5f6f8;--fg:#1a1d24;--mut:#697086;--line:#e1e4ea;--buy:#1a8a55;--sell:#c8433f;"
          "--card:#ffffff;--accent:#2563eb;--btn:#eef0f4;--input:#ffffff;--grid:#dde1e8")


def _between(text: str, start: str, end: str) -> str:
    a = text.index(start) + len(start)
    return text[a:text.index(end, a)]


def build_html(con) -> str:
    months = report.build(con)["months"]
    data = {m: report.build(con, m) for m in months}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")

    css = _between(REPORT_PAGE, "<style>", "</style>")
    css = css.replace(" :root{--bg", " :root{color-scheme:dark;--bg", 1)
    if BG_PATH.exists():
        photo = base64.b64encode(BG_PATH.read_bytes()).decode()
        css += (f"\n:root{{--photo:url(data:image/jpeg;base64,{photo})}}\n"
                "@media (prefers-color-scheme: dark){\n"
                + photo_rules(':root:not([data-theme="light"])', "var(--photo)") + "}\n"
                + photo_rules(':root[data-theme="dark"]', "var(--photo)"))
    css += (f"\n@media (prefers-color-scheme: light){{:root:not([data-theme=\"dark\"]){{{_LIGHT};color-scheme:light}}}}\n"
            "html{background:var(--bg)}\n")

    script = REPORT_PAGE[REPORT_PAGE.rindex("<script>") + len("<script>"):REPORT_PAGE.rindex("</script>")]
    head = script.split("async function load(m){", 1)[0]
    script = (head
              + "const DATA=" + payload + ";\n"
              + "function load(m){const k=m||cur||Object.keys(DATA).slice(-1)[0];cur=k;render(DATA[k]);}\n"
              + "load();")

    return f"""<title>Итоги месяца CScalp</title>
<style>{css}</style>
<header>
 <h1>Итоги месяца</h1>
 <select id="month" onchange="load(this.value)" aria-label="Месяц"></select>
 <span class="mut" style="margin-left:auto">Снимок от {stamp}</span>
</header>
<div hidden><input id="capStart"><input id="capNow"><input id="capPeak"><input id="capPeakMonth"></div>
<main id="root"></main>
<script>{script}</script>
"""


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/report_snapshot.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(db.connect()), encoding="utf-8")
    print(out.resolve())

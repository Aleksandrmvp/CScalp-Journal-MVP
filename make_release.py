"""Build a clean zip for sharing:  python make_release.py  ->  dist/cscalp-journal-YYYY-MM-DD.zip

Only whitelisted files go in (no database, keys, tokens or statements), and the
archive is scanned for the local secrets before it is kept.
"""
from __future__ import annotations

import json
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIRS = ["cscalp_journal", "docs", "prototype"]
FILES = [".gitattributes", "README.md", "README.en.md", "LICENSE", "requirements.txt", "requirements-ctrader.txt", "requirements-ctrader-nodeps.txt", "start.bat", "Dockerfile",
         "docker-compose.yml", ".dockerignore", ".gitignore", ".env.example", "telegram.json.example",
         "ctrader.json.example", "cscalp_settings.example.json", "make_release.py"]
SKIP_PARTS = {"__pycache__"}
SKIP_NAMES: set[str] = set()
SKIP_SUFFIXES = {".pyc", ".sqlite", ".xlsx"}
FORBIDDEN = {"ctrader.json", "telegram.json", ".env", "ctrader_token.json", "cscalp_settings.json"}


def _secrets() -> list[str]:
    out = []
    for name, keys in (("ctrader.json", ("client_id", "client_secret")), ("telegram.json", ("token",))):
        try:
            data = json.loads((ROOT / name).read_text(encoding="utf-8"))
        except Exception:
            continue
        out += [str(data[k]) for k in keys if len(str(data.get(k, ""))) >= 12]
    return out


def _files():
    for f in FILES:
        if (ROOT / f).exists():
            yield ROOT / f
    for d in DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if (p.is_file() and not (SKIP_PARTS & set(p.parts)) and p.name not in SKIP_NAMES
                    and p.suffix not in SKIP_SUFFIXES):
                yield p


def main() -> int:
    out = ROOT / "dist" / f"cscalp-journal-{date.today().isoformat()}.zip"
    out.parent.mkdir(exist_ok=True)
    secrets = _secrets()
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _files():
            if p.name in FORBIDDEN:
                continue
            data = p.read_bytes()
            if any(s.encode() in data for s in secrets):
                z.close()
                out.unlink()
                print(f"СТОП: в {p.relative_to(ROOT)} найден секрет, архив не создан.")
                return 1
            z.writestr(f"cscalp-journal/{p.relative_to(ROOT).as_posix()}", data)
            count += 1
    print(f"{out}  ({count} файлов, {out.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

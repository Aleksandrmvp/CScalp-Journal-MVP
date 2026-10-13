"""Configuration for the CScalp trade-journal service."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Where the user's own files live: database, settings, keys. Kept OUTSIDE the program
    folder so replacing the program with a newer version never touches them."""
    if os.environ.get("CSCALP_DATA_DIR"):
        return Path(os.environ["CSCALP_DATA_DIR"])
    if os.environ.get("CSCALP_DB"):                       # Docker / custom setups
        return Path(os.environ["CSCALP_DB"]).parent
    base = os.environ.get("APPDATA")
    return Path(base) / "CScalpJournal" if base else Path.home() / ".cscalp-journal"


DATA_DIR = _data_dir()

# Local SQLite database (our durable archive; source logs rotate daily).
DB_PATH = Path(os.environ.get("CSCALP_DB", DATA_DIR / "journal.sqlite"))

# Files that older versions kept inside the program folder. On first start they are COPIED
# (the originals stay as a backup) to the data folder.
_LEGACY = {
    DB_PATH: ROOT / "data" / "journal.sqlite",
    DATA_DIR / "ctrader_token.json": ROOT / "data" / "ctrader_token.json",
    DATA_DIR / "telegram.json": ROOT / "telegram.json",
    DATA_DIR / "ctrader.json": ROOT / "ctrader.json",
    DATA_DIR / "cscalp_settings.json": ROOT / "cscalp_settings.json",
}


def _migrate_legacy() -> None:
    for new, old in _LEGACY.items():
        try:
            if old.exists() and not new.exists() and old.resolve() != new.resolve():
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
        except OSError:
            pass


_migrate_legacy()

# Optional user settings (log folder, instrument steps, commission model). See
# cscalp_settings.example.json; environment variables win over the file.
SETTINGS_PATH = Path(os.environ.get("CSCALP_SETTINGS", DATA_DIR / "cscalp_settings.json"))


def _load_user_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise SystemExit(f"Не удалось прочитать {SETTINGS_PATH}: {e}")


USER = _load_user_settings()

# Root of the terminal's per-day HTML logs.
LOG_ROOT = Path(
    os.environ.get("CSCALP_LOG_ROOT")
    or USER.get("log_root")
    or r"C:\Program Files (x86)\FSR Launcher\SubApps\CS\Log"
)

# Re-parse interval for the ingest loop, seconds. Logs are small; a full
# idempotent re-parse + upsert every few seconds is simpler than byte-tailing.
POLL_SECONDS = float(os.environ.get("CSCALP_POLL_SECONDS", "2"))

# Maker/taker derivation: an order that rests in the book shorter than this
# before (dis)appearing is treated as an aggressive TAKER fill; longer = MAKER.
# Derived heuristic — validate against known trades.
TAKER_MAX_RESTING_SECONDS = float(os.environ.get("CSCALP_TAKER_MAX_RESTING", "1.0"))

# Dashboard bind.
WEB_HOST = os.environ.get("CSCALP_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("CSCALP_WEB_PORT", "8777"))

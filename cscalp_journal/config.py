"""Configuration for the CScalp trade-journal service."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Optional user settings (log folder, instrument steps, commission model). See
# cscalp_settings.example.json; environment variables win over the file.
SETTINGS_PATH = Path(os.environ.get("CSCALP_SETTINGS", ROOT / "cscalp_settings.json"))


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

# Local SQLite database (our durable archive; source logs rotate daily).
DB_PATH = Path(os.environ.get("CSCALP_DB", Path(__file__).resolve().parent.parent / "data" / "journal.sqlite"))

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

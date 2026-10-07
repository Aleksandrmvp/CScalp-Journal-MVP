"""Telegram Bot API posting (stdlib only).

Config lives in ./telegram.json at the project root:
    {"token": "...", "chat_id": "@channel or -100...", "enabled": true}
Nothing is posted unless `enabled` is true and both token and chat_id are set.

Usage:
    python -m cscalp_journal.telegram getme         # validate token
    python -m cscalp_journal.telegram updates       # show recent chats (to find chat_id)
    python -m cscalp_journal.telegram test           # send a test message
    python -m cscalp_journal.telegram send "текст"  # send arbitrary text
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "telegram.json"


def load_config() -> dict:
    """Config from telegram.json, with env-var fallback (for Docker / no file).

    File keys win; env fills what's missing: TG_TOKEN, TG_CHAT_ID, TG_ENABLED.
    This lets the bot be configured purely by environment (12-factor) without
    committing a secret file.
    """
    cfg: dict = {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    if not cfg.get("token") and os.environ.get("TG_TOKEN"):
        cfg["token"] = os.environ["TG_TOKEN"]
    if not cfg.get("chat_id") and os.environ.get("TG_CHAT_ID"):
        cfg["chat_id"] = os.environ["TG_CHAT_ID"]
    if "enabled" not in cfg and os.environ.get("TG_ENABLED"):
        cfg["enabled"] = os.environ["TG_ENABLED"].lower() in ("1", "true", "yes")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def is_enabled() -> bool:
    c = load_config()
    return bool(c.get("enabled") and c.get("token") and c.get("chat_id"))


def _api(method: str, params: dict) -> dict:
    c = load_config()
    token = c.get("token")
    if not token:
        return {"ok": False, "error": "no token"}
    data = urllib.parse.urlencode(params).encode()
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        with urllib.request.urlopen(url, data=data, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_message(text: str, parse_mode: str = "HTML") -> dict:
    c = load_config()
    chat = c.get("chat_id")
    if not chat:
        return {"ok": False, "error": "no chat_id"}
    return _api("sendMessage", {
        "chat_id": chat, "text": text, "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
    })


def _cli() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "getme"
    if cmd == "getme":
        print(_api("getMe", {}))
    elif cmd == "updates":
        r = _api("getUpdates", {})
        for u in r.get("result", []):
            chat = (u.get("channel_post") or u.get("message") or {}).get("chat")
            if chat:
                print(f"id={chat.get('id')} type={chat.get('type')} "
                      f"title={chat.get('title')!r} username={chat.get('username')}")
        if not r.get("result"):
            print("нет обновлений (добавьте бота админом в канал и отправьте туда сообщение)")
    elif cmd == "test":
        print(send_message("✅ Тест: бот подключён к каналу."))
    elif cmd == "send":
        print(send_message(sys.argv[2]))
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()

"""Ingest: parse log day-folders into SQLite. One-shot or continuous loop.

Usage:
    python -m cscalp_journal.ingest            # backfill all day-folders once
    python -m cscalp_journal.ingest --today    # only today's folder, once
    python -m cscalp_journal.ingest --loop     # follow today's folder forever
"""
from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path

from . import config, db
from .parser import parse_day


def today_dirname() -> str:
    return date.today().strftime("%d.%m.%Y")


def ingest_dir(con, day_dir: Path) -> dict:
    trades, lives, positions, cmds, money = parse_day(day_dir)
    return db.upsert(con, trades, lives, positions, cmds, money)


def ingest_all(con) -> None:
    for d in sorted(config.LOG_ROOT.glob("*")):
        if d.is_dir() and _is_day(d.name):
            stats = ingest_dir(con, d)
            print(f"[{d.name}] {stats}")


def _is_day(name: str) -> bool:
    try:
        from datetime import datetime
        datetime.strptime(name, "%d.%m.%Y")
        return True
    except ValueError:
        return False


def loop(con) -> None:
    print(f"tailing {config.LOG_ROOT} every {config.POLL_SECONDS}s (Ctrl+C to stop)")
    while True:
        day_dir = config.LOG_ROOT / today_dirname()
        if day_dir.is_dir():
            stats = ingest_dir(con, day_dir)
            new = {k: v for k, v in stats.items() if v}
            if new:
                print(f"[{time.strftime('%H:%M:%S')}] +{new}")
        # NOTE: Telegram posting is manual-only (📢 buttons on the bundle page).
        # Auto-posting was intentionally removed — do not call notify.check_and_post here.
        time.sleep(config.POLL_SECONDS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", action="store_true", help="only today's folder, once")
    ap.add_argument("--loop", action="store_true", help="follow today's folder forever")
    args = ap.parse_args()

    con = db.connect()
    if args.loop:
        loop(con)
    elif args.today:
        d = config.LOG_ROOT / today_dirname()
        print(ingest_dir(con, d) if d.is_dir() else f"no folder {d}")
    else:
        ingest_all(con)


if __name__ == "__main__":
    main()

# CScalp Journal

[Русский](README.md) · **English**

A trade journal for MOEX FORTS scalping done through the **CScalp** terminal (running inside FSR Launcher / a prop-dealing gateway). It passively reads the terminal's own HTML debug logs and builds an interactive diary: quotes, placed orders (maker/taker), fills, open positions, arbitrage bundles with PnL and commissions, and manual publishing of trades to a Telegram channel.

> **Disclaimer.** An unofficial, personal-use tool. It works **read-only** on the debug logs the terminal writes itself — it does not interfere with the terminal, does not touch the protocol, and never sends orders. Not affiliated with CScalp, FSR, or the exchange. Use at your own risk.

## Screenshots

Home — positions, arbitrage bundles, action journal:

![Dashboard](docs/screenshots/dashboard.webp)

Bundle page — spread formula, PnL trajectory, per-trade breakdown:

![Bundle page](docs/screenshots/bundle.webp)

## Documentation

Detailed UI guide, in Russian (every screen and field): [docs/РУКОВОДСТВО.md](docs/РУКОВОДСТВО.md).

## Features

- **Action journal** — one timeline: order placements (SUBMIT), cancels (CANCEL), fills (FILL). Filters by instrument / event / side / liquidity, with pagination.
- **Maker/taker** — derived from how long an order rests in the book (there's no flag in the logs).
- **Open positions** — taken from the terminal's own reported state (`OnPositionUpdate`) as the source of truth, not reconstructed from fills.
- **Arbitrage bundles** — assembled manually from fills plus external legs (forex hedge). Segmented into individual arb trades (open→close), with RUB PnL (via price-step value), commission, entry spread by an editable formula (TradingView notation), and a synthetic price when no forex leg is present.
- **Commissions** — prop fee model (maker 0.18 RUB/lot, taker = exchange fee ×1.8 + 0.18); the exchange fees are calibrated against the broker's real fee export.
- **Telegram** — manual publishing of arb-trade opens/closes to a channel in your channel's format.

## Requirements

- The CScalp / FSR Launcher terminal writing HTML logs (default `C:\Program Files (x86)\FSR Launcher\SubApps\CS\Log`).
- For Docker: Docker Desktop (Windows/macOS/Linux).
- For a local run: Python 3.12+.

## Quick start — Docker (recommended)

```bash
git clone https://github.com/Aleksandrmvp/CScalp-Journal-MVP.git cscalp_journal
cd cscalp_journal
cp .env.example .env        # edit the log path and (optionally) Telegram
docker compose up -d --build
```

Dashboard: <http://localhost:8777>. Container logs: `docker compose logs -f`, stop: `docker compose down`.

The container mounts the log folder **read-only**, keeps the database in `./data`, and runs in `Europe/Moscow` (important: log folder names and "today" are resolved by local time).

## Quick start — local (Windows)

```bash
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m cscalp_journal.web
```

The dashboard comes up at <http://127.0.0.1:8777> with a background log-ingest loop.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `CSCALP_LOG_ROOT` | `C:\...\CS\Log` | Folder with the terminal's per-day HTML logs |
| `CSCALP_DB` | `data/journal.sqlite` | Path to the SQLite archive |
| `CSCALP_WEB_HOST` / `CSCALP_WEB_PORT` | `127.0.0.1` / `8777` | Dashboard bind address |
| `CSCALP_POLL_SECONDS` | `2` | Log re-ingest interval |
| `TG_TOKEN` / `TG_CHAT_ID` | — | Telegram bot (optional); or a `telegram.json` file |

In Docker the host log path is set via `CSCALP_LOG_ROOT_HOST` in `.env` (inside the container it's always `/logs`).

## Telegram (optional)

1. Create a bot with [@BotFather](https://t.me/BotFather) and add it as an admin to the channel.
2. Set `TG_TOKEN` and `TG_CHAT_ID` in `.env` — **or** copy `telegram.json.example` to `telegram.json` and fill it in.
3. Publishing is manual via the 📢 buttons on a bundle page (there's no auto-posting).

## How it works

The terminal writes human-readable debug logs that decode the prop-dealing binary protocol. The parser (`cscalp_journal/parser.py`) extracts trades, orders, positions and commissions with regexes; the ingest stores them in SQLite idempotently (dedup by TradeID). FastAPI serves the dashboard and a JSON API; all analytics live in `views.py`.

Parsing quirks are documented in the code: reconnect dedup, filtering the startup position snapshot, the dispatcher log rotating into the session-start folder (`Dispatcher_XDSD_00N.html`), and the ru-locale number format.

## Layout

```
cscalp_journal/
  parser.py       HTML log parsing
  ingest.py       SQLite ingest (one-shot / loop)
  db.py           schema and upserts
  views.py        analytics: positions, journal, bundles, summary
  instruments.py  price-step values (point -> RUB)
  formula.py      spread-formula evaluator + synthetics
  commission.py   prop commission model
  telegram.py     channel posting (stdlib)
  web.py          FastAPI dashboard + HTML
prototype/        original PowerShell parser prototype
Dockerfile, docker-compose.yml
```

## Security

`telegram.json`, `.env` and `data/` are in `.gitignore` and **must never be committed**. The bot token is a secret; if it leaks, revoke/reissue it via @BotFather.

## License

MIT — see [LICENSE](LICENSE).

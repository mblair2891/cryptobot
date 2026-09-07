# AetherGrid

Self-hosted, **non-custodial** GRID trading system for Coinbase Advanced Trade, with an autonomous operator that can start, trail, pause, re-center, and stop bots.

**Repository:** [https://github.com/mblair2891/cryptobot](https://github.com/mblair2891/cryptobot)

This is **not** a custody wallet, **not** a signal-copy service, and **not** financial advice. Default mode is **demo** (synthetic market, zero keys). You can lose money. Grids fail in strong trends.

```
funds never leave your Coinbase account
the app only places and cancels trades
transfer / withdraw APIs are blocked in code
```

## What you get

Bitsgap-style range grid (v1, Coinbase Advanced spot only):

- Geometric or arithmetic ladder
- Levels **or** percent step
- Equal-quote (default) or equal-base sizing
- Buy below mark / sell above mark, recycle on fill
- Trailing up / down
- Take-profit and stop-loss
- Add funds and live range edits (cancel + replace)
- Pump/dump and breakout protection
- Inventory cap, error cooldown
- Demo venue (synthetic GBM prices, preloaded bots) plus paper and live venues that share the same grid state machine
- Multi-bot manager, SQLite (Postgres-ready), FastAPI + HTMX dashboard, `aethergrid` CLI
- Deterministic AI operator; optional SpaceXAI / xAI LLM only ranks pairs and explains

## Architecture

```
Dashboard / CLI / REST
         │
    FastAPI control plane
         │
    Bot manager  ←  hard risk limits (AI cannot disable)
         │
    Grid engine (pure state machine)
         │
    Venue adapter ── demo (synthetic)  |  paper (public ticker)  |  Coinbase live
         │
    SQLite / Postgres journal (orders, fills, PnL, AI decisions)
```

Demo, paper, and live share **one** matching engine. The venue adapter is the only switch.

## Modes

| Mode | Keys | Network | Orders |
|---|---|---|---|
| **demo** (default) | none | none to Coinbase | simulated against synthetic prices |
| **paper** | none | public Coinbase market data only | simulated fills |
| **live** | CDP view+trade | private REST/WS | real Advanced Trade orders |

The dashboard header switches **Demo | Paper | Live**. Demo ignores live keys. Demo → Live is blocked until CDP keys are in **server env** and you type `I UNDERSTAND THE RISK`. The browser never receives the private key. Vercel is demo-only and refuses Live. Leaving Live asks whether to cancel open orders (default yes).

## Quick start (demo, zero keys)

Clone this repo and run demo. No Coinbase API key, no LLM key, no `.env` secrets.

Requires Python 3.12+.

```bash
git clone https://github.com/mblair2891/cryptobot.git
cd cryptobot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
aethergrid init
aethergrid demo
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The banner reads **DEMO — simulated funds and prices. Not connected to Coinbase.** Two grids are already running (one ranging, one trending against), one is paused, and the AI journal has sample decisions.

Or:

```bash
docker compose up --build
```

`docker compose up` runs **demo mode** with no Coinbase keys and no private API calls.

## Vercel = demo UI only

https://cryptobot-zeta.vercel.app/ is a **serverless demo** of the HTMX dashboard. It is not a trading host.

- Forced `MODE=demo`. Live keys are ignored even if they exist in the Vercel project.
- SQLite lives in `/tmp` and is re-seeded on cold start. Do not expect durable bots.
- There is **no background worker**. The UI polls `POST /api/tick` so the synthetic grid advances per request.
- Real **paper** and **live** trading: run locally, Docker Compose, Railway, or Render — not Vercel.

```bash
# this repo on Vercel (already wired)
# vercel.json + api/index.py + requirements-vercel.txt
```

Local / Docker paths are unchanged: `aethergrid demo`, `aethergrid paper`, `docker compose up`.

```bash
aethergrid demo reset     # wipe demo DB; next start reseeds
aethergrid mode show      # current mode + promotion rules
aethergrid paper          # public tickers, still no live orders
```

Create a bot once you know a range that brackets the live mark:

```bash
python scripts/seed_paper.py
# then POST the printed payload
curl -s http://127.0.0.1:8000/api/products | head
curl -s -X POST http://127.0.0.1:8000/api/bots \
  -H 'content-type: application/json' \
  -d '{"product_id":"BTC-USD","investment":"1000","lower_price":"...","upper_price":"...","grid_levels":11}'
```

The AI operator will also propose bots on liquid USD/USDC pairs when range quality clears its filters. Pause it from the AI page if you want a human-only desk.

## Tests

```bash
pytest -q
```

Coverage includes ladder math, fill recycling, trailing, stop-loss, paper fills, reconcile, risk trips, AI validators, demo adapter fills, demo seed data, and mode isolation (live Coinbase client is never constructed in demo).

## Coinbase live trading (explicit, opt-in)

Live mode will **refuse to start** unless all of the following are true:

1. You have left demo (`MODE` is not `demo`) and have run **paper** at least once
2. `MODE=live`
3. `LIVE_CONFIRMED=true`
4. You ran `aethergrid live --i-understand-the-risk` and typed `I UNDERSTAND THE RISK`
5. CDP API credentials with **view + trade only**

Keys sitting in `.env` while `MODE=demo` are ignored. Demo cannot place live orders.

### Create a trade-only CDP key

1. Open [Coinbase Developer Platform](https://portal.cdp.coinbase.com/access/api).
2. Create an **API key** for Advanced Trade.
3. Grant **View** and **Trade**. Do **not** enable Transfer or Withdraw.
4. Copy the key *name* (`organizations/{org}/apiKeys/{id}`) and the EC private key PEM.
5. Put them in `.env`:

```
COINBASE_API_KEY_NAME=organizations/.../apiKeys/...
COINBASE_API_PRIVATE_KEY="-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----\n"
```

Never commit `.env`. AetherGrid will not call withdraw, transfer, send, or convert endpoints; those SDK methods are blocked.

Then:

```bash
aethergrid live --i-understand-the-risk
# edit .env: MODE=live and LIVE_CONFIRMED=true
aethergrid serve
```

Start with `MAX_LIVE_NOTIONAL` at a number you can afford to lose.

## CLI

```
aethergrid init
aethergrid demo
aethergrid demo reset
aethergrid mode show
aethergrid paper
aethergrid live --i-understand-the-risk
aethergrid serve
aethergrid worker
aethergrid bots list|show|stop
aethergrid backtest --product BTC-USD --days 90
aethergrid ai status
aethergrid logs
```

## AI operator

Every N seconds the operator ingests balances, orders, candles, PnL, spread, volatility, range quality, inventory skew, and errors. It may emit **only**:

`noop` · `propose_new_bot` · `reconfigure_bot` · `trail_up` · `trail_down` · `pause_entries` · `resume` · `add_funds` · `take_profit_close` · `stop_loss_close` · `flatten_and_archive`

Execution still has to pass pydantic validation **and** hard risk limits. The operator cannot disable those limits.

Without an LLM key the policy is fully deterministic. With `XAI_API_KEY` (or `LLM_API_KEY`) it uses SpaceXAI (`https://api.x.ai/v1`, default model `grok-4.6`) **only** to rank pairs and write reasons. Numbers are re-validated.

## Hard risk limits

Env-configurable; the AI cannot turn them off:

| Variable | Default | On trip |
|---|---|---|
| `MODE` | `paper` | live refused |
| `MAX_LIVE_NOTIONAL` | 500 | cancel, stop AI |
| `MAX_BOTS` | 5 | reject new bots |
| `MAX_OPEN_ORDERS_TOTAL` / per-bot | 200 / 80 | pause |
| `MAX_DAILY_REALIZED_LOSS` | 50 | kill |
| `MAX_DRAWDOWN_PCT` | 0.15 | kill |
| `MIN_CASH_RESERVE` | 0 | stop new entries |
| `KILL_SWITCH` file or `POST /api/kill` | off | cancel all, stop AI, optional flatten |

## Backtest caveat

`aethergrid backtest --product BTC-USD --days 90` replays Coinbase candles with a conservative fill model (touch + fee + half spread). **Candle backtests overstate grids.** They cannot model queue position, intra-bar noise, or gaps.

## Dashboard

- Overview — equity, daily PnL, mode, kill switch
- Bots — create / pause / stop, ladder, open orders, inventory
- AI — last decisions, proposed vs executed
- Journal — fills and PnL ticks
- Settings — mode and limits; **no raw secrets**

## Postgres (optional)

```bash
docker compose --profile postgres up
# DATABASE_URL=postgresql+asyncpg://aethergrid:aethergrid@postgres:5432/aethergrid
```

Alembic lives in `alembic/`. `aethergrid init` also creates the SQLite schema directly.

## Risk warning

Grid trading is not a money printer. A range grid **loses** when price trends through the band and inventory piles up on the wrong side. Trailing and stop-losses reduce, not eliminate, that. Paper first. Size live as if the allocation can go to zero.

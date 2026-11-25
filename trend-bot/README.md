# trendbot

A small, rules-based crypto trading bot. It needs no AI to run: it's plain Python
on a schedule, and everything it does is written down in this folder.

**What it trades:** a long-only trend rule on ~20 large coins, daily bars.
A coin is held only while its close is above its 100-day average. Positions are
sized so the whole portfolio targets a fixed volatility (15% a year by default).
Rebalancing is weekly; exits are checked daily; every position has a stop.

**What it is not:** a money machine. Simple trend following has decades of
evidence behind it, but in crypto it still has long flat stretches and 20–30%
drawdowns, and it can stop working. The research step below exists to tell you
honestly whether it works on real data *before* you risk anything.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml      # then edit it
python -m pytest -q                     # 18 tests, should all pass
```

In `config.yaml`, set at least:
- `exchange` / `quote`: where you will trade. **It must be legal where you live**
  (US residents: not Binance.com, no offshore perpetuals).
- `universe`: drop coins your exchange doesn't list.
- `costs.fee_per_side`: your real fee tier.
- `holdout_start`: 12 months before the day you start. Never move it afterwards.

## The four steps

| Step | Command | What happens |
|---|---|---|
| 1. Data | `python -m trendbot download` | Daily candles to `data/raw`, checked copies in `data/clean`, issues in `data/clean/report.md`, sources in `data/sources.md`. Gaps stay gaps. |
| 2. Research | `python -m trendbot research` | Runs only on data **before** `holdout_start`. Report in `reports/`. Exit code 0 = passed, 2 = failed. |
| 3. Holdout | `python -m trendbot holdout` | Tests the frozen settings **once** on the last 12 months. Refuses to run the same settings twice. Logged in `holdout_log.md`. |
| 4. Paper, then live | `python -m trendbot run` | One trading pass. Paper by default. |

### What research checks

| Check | Pass if |
|---|---|
| Enough trades | 150+ entries |
| Costs | Profitable at real costs **and** at 2x costs |
| Walk-forward | Best grid setting on each past year, tested on the next quarter: 60%+ of quarters positive |
| No cliff | Nearby settings (SMA, vol window, stop ±25%) also profitable |
| Deflated Sharpe | ≥ 0.90, counting **every variant ever tried** (`trials.csv` never resets) |
| Monte Carlo | Worst-5% reshuffled drawdown is inside the kill switch |
| Baselines | Beats BTC buy-and-hold on return ÷ drawdown, and beats 90% of random-entry runs with the same sizing and exits |

If it fails, that's the answer. Don't keep tweaking until it passes: every
tweak is logged and makes the luck check harder, on purpose.

## Running it

Paper trading needs no API keys. Schedule it with cron on an always-on server:

```cron
# daily trading pass, a few minutes after the UTC daily close
7 0 * * *  cd /path/to/trend-bot && .venv/bin/python -m trendbot run     >> logs/cron.log 2>&1
# hourly health check
23 * * * * cd /path/to/trend-bot && .venv/bin/python -m trendbot monitor >> logs/cron.log 2>&1
```

Alerts go to `logs/alerts.log`, and to Telegram if you set `TELEGRAM_BOT_TOKEN`
and `TELEGRAM_CHAT_ID`. Every run starts with `MODE: PAPER` or `MODE: LIVE`.
Every fill goes to `trades.csv`.

**Paper-trade for at least 3 months and 50 trades** and compare against the
backtest for the same period before considering real money.

### Going live

Only after paper trading matches the backtest:

1. Create an API key with **trading only, withdrawals off, IP-locked** to your server.
2. Put it in the environment, never in files you commit:
   `TRENDBOT_API_KEY`, `TRENDBOT_API_SECRET` (and `TRENDBOT_API_PASSWORD` if your exchange uses one).
3. Set `mode: live` in config **and** `TRENDBOT_GO_LIVE=yes` in the environment. Both are required.
4. Start with about 25% of the money you plan to use.

## Safety rules built into the code

- **Stops on the exchange.** After each run every position gets a stop-limit sell
  resting on the exchange. If one can't be placed, the position is sold. If the
  exchange's API can't hold stop orders at all, the bot refuses to buy.
- **Stops only move up.** They trail at 3 daily standard deviations below the close.
- **Risk per trade.** A coin's size is capped so that hitting its stop loses at most 1% of equity.
- **Daily loss limit (3%).** No new buying until the next UTC day.
- **Kill switch (20% from the high).** Trading stops, stops stay in place, and it
  waits for you. To resume, remove `"killed"` from `state/live.json` or `state/paper.json`.
- **No leverage by default** (`max_gross: 1.0`), long only, no averaging down.
- **Same code everywhere.** Backtest, paper and live call the same strategy functions.

## Known limits (read these)

- **Survivorship bias.** The universe is a fixed list. Choose coins that were big
  *at research_start*, not today's winners. Coins that stop trading are exited at
  their last price minus `delist_haircut` (30%), which is an assumption, not data.
- **Stop gaps.** A stop-limit order can be skipped in a crash. The backtest fills
  stops at the lower of the open and the stop, but real fills can be worse.
- **Execution.** Rebalance orders are immediate-or-cancel limit orders capped at
  0.3% from the best price, so they pay the taker fee. A partly filled order is
  topped up at the next weekly rebalance, not straight away.
- **Short gap without stops.** On rebalance days stops are cancelled, orders are
  placed, then stops are put back, which leaves a window of a few seconds.
- **Exchange support.** Stop orders go through ccxt's `create_stop_limit_order`.
  Check that your exchange supports it in paper mode before going live.
- **Live mode has only been tested against simulated exchanges**, not a real
  account. Watch the first live runs closely.

## Files

```
config.yaml       your settings (git-ignored)
data/             raw + checked candles, sources.md, data report
reports/          research reports
trials.csv        every variant ever tested (never delete)
holdout_log.md    one-time holdout results (append-only)
trades.csv        every paper and live fill
state/            bot state: cash, positions, stops, equity high, kill flag
logs/alerts.log   every alert
```

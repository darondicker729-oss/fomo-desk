"""Paper and live trading. Runs once per day after the UTC daily close.

Same strategy code as the backtest. Safety rules enforced here:
* MODE: PAPER / MODE: LIVE is the first log line and alert of every run.
* Live needs `mode: live` in config AND env TRENDBOT_GO_LIVE=yes.
* Kill switch at the drawdown limit: no more trading, stops are left in place.
* Daily loss limit: no new buying until the next UTC day.
* No stop on the exchange, no position: if a stop can't be placed, the
  position is sold straight away.
* Stops are only ever raised, never lowered.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .config import Config
from .data import DAY_MS, drop_unclosed, frames_to_panel, ohlcv_to_frame
from .strategy import daily_vol, decide, stop_level, target_weights

log = logging.getLogger("trendbot")

TRADE_FIELDS = ["trade_id", "time", "mode", "symbol", "side", "qty", "price", "fee",
                "reason", "target_weight", "equity", "test_id"]


# ---------------------------------------------------------------- alerts

class Alerts:
    def __init__(self, cfg: Config):
        self.path = cfg.p("logs", "alerts.log")
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID")

    def send(self, text: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self.path.open("a") as f:
            f.write(f"{stamp} {text}\n")
        log.info(text)
        if self.token and self.chat:
            try:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat, "text": text}, timeout=15)
            except requests.RequestException as e:  # alerts must never crash the bot
                log.error("Telegram failed: %s", e)


# ---------------------------------------------------------------- state

class State:
    def __init__(self, path: Path):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {}

    def __getitem__(self, k):
        return self.d[k]

    def get(self, k, default=None):
        return self.d.get(k, default)

    def __setitem__(self, k, v):
        self.d[k] = v

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, indent=2, sort_keys=True))
        tmp.replace(self.path)


# ---------------------------------------------------------------- brokers

class PaperBroker:
    """Simulated account. Fills at the reference price with slippage and fees;
    stops are simulated against the daily lows the same way the backtest does."""

    mode = "PAPER"

    def __init__(self, state: State, cfg: Config):
        self.s = state
        self.fee = cfg.fee_per_side
        self.slip = cfg.slippage_per_side
        self.s.d.setdefault("cash", cfg.paper_start_cash)
        self.s.d.setdefault("qty", {})
        self.s.d.setdefault("stops", {})

    def can_place_stops(self) -> bool:
        return True

    def holdings(self) -> tuple[float, dict[str, float]]:
        return float(self.s["cash"]), {k: float(v) for k, v in self.s["qty"].items() if v > 0}

    def buy(self, sym, qty, ref):
        px = ref * (1 + self.slip)
        qty = min(qty, self.s["cash"] / (px * (1 + self.fee)))
        if qty <= 0:
            return None
        fee = qty * px * self.fee
        self.s["cash"] -= qty * px + fee
        self.s["qty"][sym] = self.s["qty"].get(sym, 0.0) + qty
        return {"qty": qty, "price": px, "fee": fee}

    def sell(self, sym, qty, ref, px=None):
        qty = min(qty, self.s["qty"].get(sym, 0.0))
        if qty <= 0:
            return None
        px = px if px is not None else ref * (1 - self.slip)
        fee = qty * px * self.fee
        self.s["cash"] += qty * px - fee
        self.s["qty"][sym] -= qty
        if self.s["qty"][sym] <= 1e-12:
            del self.s["qty"][sym]
        return {"qty": qty, "price": px, "fee": fee}

    def set_stop(self, sym, qty, stop) -> bool:
        self.s["stops"][sym] = stop
        return True

    def cancel_stop(self, sym) -> None:
        self.s["stops"].pop(sym, None)

    def stops(self) -> dict[str, float]:
        return dict(self.s["stops"])

    def process_stops(self, panel, after: pd.Timestamp | None) -> list[dict]:
        fills = []
        for sym, stop in list(self.s["stops"].items()):
            if sym not in panel["low"]:
                continue
            lows, opens = panel["low"][sym], panel["open"][sym]
            for day in lows.index:
                if after is not None and day <= after:
                    continue
                if not np.isnan(lows[day]) and lows[day] <= stop:
                    o = opens[day]
                    px = min(o, stop) if not np.isnan(o) else stop
                    f = self.sell(sym, self.s["qty"].get(sym, 0.0), px, px=px * (1 - self.slip))
                    self.cancel_stop(sym)
                    if f:
                        fills.append({**f, "symbol": sym, "side": "sell", "reason": f"stop hit {day.date()}"})
                    break
        return fills


class LiveBroker:
    """Real exchange through ccxt. Orders are limit orders with a price cap
    (immediate-or-cancel); stops are stop-limit orders resting on the exchange."""

    mode = "LIVE"

    def __init__(self, exchange, state: State, cfg: Config):
        self.ex = exchange
        self.s = state
        self.quote = cfg.quote
        self.max_slip = cfg.execution["max_slippage"]
        self.gap = cfg.execution["stop_limit_gap"]
        self.s.d.setdefault("stop_orders", {})  # sym -> {"id":..., "price":...}
        self.m = {u: cfg.trade_symbol(u) for u in cfg.universe}  # universe -> exchange symbol

    def can_place_stops(self) -> bool:
        has = getattr(self.ex, "has", {}) or {}
        return bool(has.get("createStopLimitOrder") or has.get("createStopOrder"))

    def holdings(self):
        bal = self.ex.fetch_balance()
        total = bal.get("total", {})
        cash = float(total.get(self.quote, 0.0) or 0.0)
        qty = {}
        for sym in self.m:
            base = sym.split("/")[0]
            q = float(total.get(base, 0.0) or 0.0)
            if q > 0:
                qty[sym] = q
        return cash, qty

    def _order(self, usym, side, qty, ref):
        sym = self.m[usym]
        t = self.ex.fetch_ticker(sym)
        if side == "buy":
            price = (t.get("ask") or ref) * (1 + self.max_slip)
        else:
            price = (t.get("bid") or ref) * (1 - self.max_slip)
        amount = float(self.ex.amount_to_precision(sym, qty))
        if amount <= 0:
            return None
        o = self.ex.create_order(sym, "limit", side, amount,
                                 float(self.ex.price_to_precision(sym, price)),
                                 {"timeInForce": "IOC"})
        o = self.ex.fetch_order(o["id"], sym) if o.get("filled") is None else o
        filled = float(o.get("filled") or 0.0)
        if filled <= 0:
            return None
        fee = (o.get("fee") or {}).get("cost") or 0.0
        return {"qty": filled, "price": float(o.get("average") or o.get("price")), "fee": float(fee)}

    def buy(self, sym, qty, ref):
        return self._order(sym, "buy", qty, ref)

    def sell(self, sym, qty, ref, px=None):
        return self._order(sym, "sell", qty, ref)

    def set_stop(self, usym, qty, stop) -> bool:
        sym = self.m[usym]
        try:
            amount = float(self.ex.amount_to_precision(sym, qty))
            trigger = float(self.ex.price_to_precision(sym, stop))
            limit = float(self.ex.price_to_precision(sym, stop * (1 - self.gap)))
            o = self.ex.create_stop_limit_order(sym, "sell", amount, limit, trigger)
            self.s["stop_orders"][usym] = {"id": o["id"], "price": stop}
            return True
        except Exception as e:  # noqa: BLE001 - any failure means "no stop"
            log.error("Stop order failed for %s: %s", sym, e)
            return False

    def cancel_stop(self, usym) -> None:
        info = self.s["stop_orders"].pop(usym, None)
        if not info:
            return
        try:
            self.ex.cancel_order(info["id"], self.m[usym])
        except Exception as e:  # noqa: BLE001 - already filled or gone
            log.warning("Cancel stop %s: %s", sym, e)

    def stops(self) -> dict[str, float]:
        return {k: v["price"] for k, v in self.s["stop_orders"].items()}

    def process_stops(self, panel, after) -> list[dict]:
        fills = []
        for sym, info in list(self.s["stop_orders"].items()):
            try:
                o = self.ex.fetch_order(info["id"], self.m[sym])
            except Exception as e:  # noqa: BLE001
                log.warning("Could not check stop %s: %s", sym, e)
                continue
            if o.get("status") == "closed":
                fills.append({"symbol": sym, "side": "sell", "qty": float(o.get("filled") or 0),
                              "price": float(o.get("average") or o.get("price") or 0),
                              "fee": float((o.get("fee") or {}).get("cost") or 0),
                              "reason": "stop hit"})
                self.s["stop_orders"].pop(sym, None)
        return fills


# ---------------------------------------------------------------- runner

class Runner:
    def __init__(self, cfg: Config, market, broker, state: State, alerts: Alerts,
                 test_id: str = "", now_ms: int | None = None):
        self.cfg, self.market, self.b, self.s, self.alerts = cfg, market, broker, state, alerts
        self.p = cfg.strategy
        self.test_id = test_id
        self.now_ms = now_ms

    def panel(self) -> dict[str, pd.DataFrame]:
        n = max(self.p.sma, self.p.mom_lookback, self.p.vol_lookback) + 40
        since = (self.now_ms or int(time.time() * 1000)) - n * DAY_MS
        frames = {}
        for sym in self.cfg.universe:
            rows = drop_unclosed(self.market.fetch_ohlcv(self.cfg.trade_symbol(sym), "1d",
                                                         since=since, limit=n + 5), self.now_ms)
            if rows:
                frames[sym] = ohlcv_to_frame(rows)
        if not frames:
            raise RuntimeError("No market data returned")
        return frames_to_panel(frames)

    def run_once(self) -> dict:
        mode = self.b.mode
        self.alerts.send(f"MODE: {mode} | trendbot run starting")
        panel = self.panel()
        close = panel["close"].reindex(columns=self.cfg.universe)
        bar = close.index[-1]
        if self.s.get("last_bar") == str(bar.date()):
            self.alerts.send(f"MODE: {mode} | bar {bar.date()} already processed, nothing to do")
            return {"skipped": True}

        last_bar = pd.Timestamp(self.s["last_bar"]) if self.s.get("last_bar") else None
        fills = [dict(f, target_weight="") for f in self.b.process_stops(panel, last_bar)]

        prices = close.iloc[-1]
        cash, qty = self.b.holdings()
        equity = cash + sum(q * float(prices.get(s, np.nan)) for s, q in qty.items()
                            if not np.isnan(prices.get(s, np.nan)))
        today = datetime.fromtimestamp((self.now_ms or time.time() * 1000) / 1000, timezone.utc).date()
        high = max(float(self.s.get("equity_high", equity)), equity)
        self.s["equity_high"] = high
        if self.s.get("day") != str(today):
            self.s["day"], self.s["day_start_equity"] = str(today), equity

        kill = self.cfg.risk["kill_switch_drawdown"]
        if self.s.get("killed") or equity <= high * (1 - kill):
            self.s["killed"] = True
            self._log_trades(fills, equity)
            self.s.save()
            self.alerts.send(f"MODE: {mode} | KILL SWITCH: equity {equity:,.2f} is "
                             f"{equity / high - 1:.1%} from high {high:,.2f}. No trading. Stops left "
                             f"in place. Waiting for you (delete 'killed' from state to resume).")
            return {"killed": True, "equity": equity}

        daily_hit = equity <= float(self.s["day_start_equity"]) * (1 - self.cfg.risk["daily_loss_limit"])
        stops_ok = self.b.can_place_stops()

        tgt_all = target_weights(close, self.p)
        tgt = tgt_all.iloc[-1].to_numpy()
        px = prices.to_numpy()
        syms = list(close.columns)
        w = np.array([qty.get(s, 0.0) * px[i] / equity if equity > 0 and not np.isnan(px[i]) else 0.0
                      for i, s in enumerate(syms)])
        rebalance = bar.weekday() == self.p.rebalance_weekday
        new = decide(w, tgt, rebalance, self.p, ~np.isnan(px))
        if daily_hit or not stops_ok:
            new = np.minimum(new, w)  # exits and trims only
        if daily_hit:
            self.alerts.send(f"MODE: {mode} | daily loss limit hit: no new buying today")
        if not stops_ok:
            self.alerts.send(f"MODE: {mode} | exchange can't hold stop orders via API: no buying")

        order = sorted(range(len(syms)), key=lambda i: new[i] - w[i])  # sells first
        for i in order:
            s, d = syms[i], new[i] - w[i]
            if abs(d) < 1e-9 or np.isnan(px[i]):
                continue
            q = abs(d) * equity / px[i]
            if d < 0:
                self.b.cancel_stop(s)
                f = self.b.sell(s, q if new[i] > 0 else qty.get(s, 0.0), px[i])
            else:
                f = self.b.buy(s, q, px[i])
            if f:
                fills.append({**f, "symbol": s, "side": "sell" if d < 0 else "buy",
                              "reason": "rebalance" if rebalance else "signal exit",
                              "target_weight": round(float(new[i]), 4)})

        # refresh stops: raise only, never lower; no stop -> no position
        cash, qty = self.b.holdings()
        dv = daily_vol(close, self.p).iloc[-1].to_numpy()
        old = self.b.stops()
        for i, s in enumerate(syms):
            q = qty.get(s, 0.0)
            if q <= 0:
                if s in old:
                    self.b.cancel_stop(s)
                continue
            level = stop_level(px[i], dv[i], self.p)
            if np.isnan(level) or level <= 0:
                level = old.get(s, px[i] * (1 - 0.15))  # fallback: 15% below price
            level = max(level, old.get(s, 0.0))
            if s in old and abs(old[s] - level) < 1e-12 and self.b.mode == "PAPER":
                continue
            self.b.cancel_stop(s)
            if not self.b.set_stop(s, q, level):
                f = self.b.sell(s, q, px[i])
                self.alerts.send(f"MODE: {mode} | MISSING STOP on {s}: could not place it, sold position")
                if f:
                    fills.append({**f, "symbol": s, "side": "sell", "reason": "no stop -> exit",
                                  "target_weight": 0})

        cash, qty = self.b.holdings()
        equity = cash + sum(q * px[syms.index(s)] for s, q in qty.items() if s in syms)
        self.s["last_bar"] = str(bar.date())
        self.s["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.s["expected_qty"] = qty
        self._log_trades(fills, equity)
        self.s.save()
        held = ", ".join(f"{s} {q * px[syms.index(s)] / equity:.0%}" for s, q in qty.items()) or "cash"
        self.alerts.send(f"MODE: {mode} | bar {bar.date()} | equity {equity:,.2f} "
                         f"({equity / high - 1:+.1%} from high) | {len(fills)} fills | holding: {held}")
        return {"equity": equity, "fills": fills, "rebalance": rebalance}

    def _log_trades(self, fills, equity):
        if not fills:
            return
        path = self.cfg.p("trades.csv")
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, TRADE_FIELDS)
            if new:
                w.writeheader()
            for k, fl in enumerate(fills):
                w.writerow({
                    "trade_id": f"{int(time.time())}-{k}",
                    "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "mode": self.b.mode, "symbol": fl["symbol"], "side": fl["side"],
                    "qty": fl["qty"], "price": fl["price"], "fee": fl["fee"],
                    "reason": fl.get("reason", ""), "target_weight": fl.get("target_weight", ""),
                    "equity": round(equity, 2), "test_id": self.test_id,
                })


# ---------------------------------------------------------------- monitor

def monitor(cfg: Config, broker, state: State, alerts: Alerts, max_age_hours: float = 26) -> list[str]:
    problems = []
    last = state.get("last_run")
    if not last:
        problems.append("bot has never run")
    else:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600
        if age > max_age_hours:
            problems.append(f"last run {age:.1f}h ago (limit {max_age_hours}h)")
    if state.get("killed"):
        problems.append("kill switch is active")
    try:
        _, qty = broker.holdings()
        stops = broker.stops()
        for s, q in qty.items():
            if s not in stops:
                problems.append(f"{s} has no stop on record")
        exp = state.get("expected_qty", {}) or {}
        for s in set(exp) | set(qty):
            a, b = float(exp.get(s, 0)), float(qty.get(s, 0))
            if abs(a - b) > 0.01 * max(a, b, 1e-12):
                problems.append(f"{s} balance {b} differs from bot record {a}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"exchange check failed: {e}")
    if problems:
        alerts.send(f"MODE: {broker.mode} | MONITOR: " + "; ".join(problems))
    return problems

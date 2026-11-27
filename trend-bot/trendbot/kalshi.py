"""Kalshi favourite-longshot research (pre-registered in learning.md).

python -m trendbot.kalshi collect   # sample settled markets + pre-close prices
python -m trendbot.kalshi research  # research-period verdict
python -m trendbot.kalshi holdout   # once, after research passes
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
OUT = Path(__file__).resolve().parent.parent / "data" / "kalshi"
CUTOFF_FILE = OUT / "cutoff.json"
RESEARCH = (pd.Timestamp("2025-06-01", tz="UTC"), pd.Timestamp("2026-08-27", tz="UTC"))
HOLDOUT_START = pd.Timestamp("2026-08-27", tz="UTC")
MIN_VOLUME = 1000
LEAD = timedelta(hours=3)
FAV = 0.80
FEE_MULT = 0.07
SLIP = 0.01
PER_MONTH = 250
DAYS_PER_MONTH = 6

S = requests.Session()


def get(path, **params):
    for attempt in range(5):
        r = S.get(API + path, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2 + attempt * 2)
            continue
        r.raise_for_status()
        time.sleep(0.08)
        return r.json()
    r.raise_for_status()


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s).tz_convert("UTC") if pd.Timestamp(s).tzinfo else pd.Timestamp(s, tz="UTC")


def markets_closing(day: pd.Timestamp, cutoff: pd.Timestamp) -> list[dict]:
    """All settled single-leg binary markets closing on a UTC day."""
    path = "/historical/markets" if day + pd.Timedelta(days=1) <= cutoff else "/markets"
    params = {"limit": 1000, "min_close_ts": int(day.timestamp()),
              "max_close_ts": int((day + pd.Timedelta(days=1)).timestamp()), "mve_filter": "exclude"}
    if path == "/markets":
        params["status"] = "settled"
    out, cursor = [], None
    for _ in range(30):
        if cursor:
            params["cursor"] = cursor
        d = get(path, **params)
        out += d.get("markets", [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return [m for m in out if m.get("market_type") == "binary" and m.get("result") in ("yes", "no")
            and not m.get("mve_collection_ticker")
            and float(m.get("volume_fp") or m.get("volume") or 0) >= MIN_VOLUME]


def price_before(m: dict, cutoff: pd.Timestamp) -> float | None:
    close = _ts(m["close_time"])
    max_ts = int((close - LEAD).timestamp())
    path = "/historical/trades" if close <= cutoff else "/markets/trades"
    d = get(path, ticker=m["ticker"], max_ts=max_ts, limit=1)
    t = d.get("trades") or []
    if not t:
        return None
    return float(t[0]["yes_price_dollars"])


SERIES = ["KXNFLGAME", "KXNBAGAME", "KXMLBGAME", "KXNHLGAME", "KXNCAAFGAME", "KXNCAAMBGAME",
          "KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHLAX", "KXFED", "KXFEDDECISION", "KXCPI",
          "KXCPIYOY", "KXPAYROLLS", "KXGDP", "KXBTCD", "KXINX", "KXTSAW", "KXRT"]
PER_SERIES = 300
MAX_PAGES = 20


def series_markets(series: str) -> list[dict]:
    out = []
    for path, extra in (("/historical/markets", {}), ("/markets", {"status": "settled"})):
        cursor = None
        for _ in range(MAX_PAGES):
            params = {"limit": 1000, "series_ticker": series, **extra}
            if cursor:
                params["cursor"] = cursor
            d = get(path, **params)
            batch = d.get("markets", [])
            out += batch
            cursor = d.get("cursor")
            if not cursor or not batch:
                break
            if min(_ts(m["close_time"]) for m in batch) < RESEARCH[0]:
                break
    seen, keep = set(), []
    for m in out:
        if m["ticker"] in seen:
            continue
        seen.add(m["ticker"])
        if (m.get("market_type") == "binary" and m.get("result") in ("yes", "no")
                and float(m.get("volume_fp") or m.get("volume") or 0) >= MIN_VOLUME
                and _ts(m["close_time"]) >= RESEARCH[0]):
            keep.append(m)
    return keep


def collect() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cut = get("/historical/cutoff")
    cutoff = _ts(cut["market_settled_ts"])
    CUTOFF_FILE.write_text(json.dumps(cut))
    rng = np.random.default_rng(7)
    rows_path = OUT / "markets.csv"
    done_series = set(pd.read_csv(rows_path)["series"]) if rows_path.exists() else set()
    total = 0
    for series in SERIES:
        if series in done_series:
            print(f"{series}: already collected", flush=True)
            continue
        try:
            pool = series_markets(series)
        except requests.HTTPError as e:
            print(f"{series}: listing failed ({e})", flush=True)
            continue
        order = rng.permutation(len(pool))
        rows, tried = [], 0
        for i in order:
            if len(rows) >= PER_SERIES or tried >= PER_SERIES * 3:
                break
            m = pool[i]
            tried += 1
            try:
                p = price_before(m, cutoff)
            except requests.HTTPError:
                continue
            if p is None or not (0 < p < 1):
                continue
            rows.append({"ticker": m["ticker"], "event": m["event_ticker"], "series": series,
                         "close_time": m["close_time"], "result": m["result"], "price_yes": p,
                         "volume": float(m.get("volume_fp") or 0)})
        if rows:
            pd.DataFrame(rows).to_csv(rows_path, mode="a", header=not rows_path.exists(), index=False)
        total += len(rows)
        print(f"{series}: {len(pool)} eligible, kept {len(rows)} (tried {tried})", flush=True)
    with (OUT.parent / "sources.md").open("a") as f:
        f.write(f"| Kalshi markets | api.elections.kalshi.com | {total} markets | "
                f"downloaded {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC |\n")


# ---------------------------------------------------------------- analysis

def bets(df: pd.DataFrame, cost_mult: float = 1.0) -> pd.DataFrame:
    df = df.copy()
    df["fav_yes"] = df.price_yes >= FAV
    df["fav_no"] = (1 - df.price_yes) >= FAV
    b = df[df.fav_yes | df.fav_no].copy()
    b["price"] = np.where(b.fav_yes, b.price_yes, 1 - b.price_yes)
    b["won"] = np.where(b.fav_yes, b.result == "yes", b.result == "no")
    fee = FEE_MULT * b.price * (1 - b.price) * cost_mult
    b["cost"] = b.price + SLIP * cost_mult + fee
    b["pnl_per_dollar"] = (b.won.astype(float) - b.cost) / b.cost
    return b


def bootstrap_ci(b: pd.DataFrame, sims=5000, seed=0):
    rng = np.random.default_rng(seed)
    g = b.groupby("event")["pnl_per_dollar"].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    n = len(g)
    means = np.empty(sims)
    for i in range(sims):
        k = rng.integers(0, n, n)
        means[i] = s[k].sum() / c[k].sum()
    return np.percentile(means, [2.5, 97.5])


def report(df: pd.DataFrame, label: str):
    lines = [f"# Kalshi favourite-longshot: {label}", ""]
    lines.append(f"{len(df)} markets, {df.event.nunique()} events, {df.series.nunique()} series, "
                 f"{df.close_time.min()[:10]} → {df.close_time.max()[:10]}")
    lines += ["", "## Calibration (price 3h before close vs how often YES won)", "",
              "| YES price | Markets | Implied | Actual |", "|---|---|---|---|"]
    bins = [0, .05, .1, .2, .35, .5, .65, .8, .9, .95, 1.0]
    df = df.assign(bucket=pd.cut(df.price_yes, bins))
    for k, g in df.groupby("bucket", observed=True):
        lines.append(f"| {k} | {len(g)} | {g.price_yes.mean():.1%} | {(g.result == 'yes').mean():.1%} |")
    b1, b2 = bets(df, 1), bets(df, 2)
    m1, m2 = b1.pnl_per_dollar.mean(), b2.pnl_per_dollar.mean()
    lo, hi = bootstrap_ci(b1) if len(b1) else (0, 0)
    ct = pd.to_datetime(b1.close_time, utc=True, format="ISO8601")
    mid = ct.min() + (ct.max() - ct.min()) / 2
    h1, h2 = b1[ct < mid].pnl_per_dollar.mean(), b1[ct >= mid].pnl_per_dollar.mean()
    by_series = b1.groupby("series").pnl_per_dollar.agg(["count", "mean"]).sort_values("count", ascending=False)
    return lines, b1, m1, m2, lo, hi, h1, h2, by_series


def research(holdout: bool = False) -> bool:
    df = pd.read_csv(OUT / "markets.csv")
    ct = pd.to_datetime(df.close_time, utc=True, format="ISO8601")
    if holdout:
        log = OUT.parent.parent / "holdout_log.md"
        if log.exists() and "kalshi-favourite" in log.read_text():
            sys.exit("Kalshi holdout already used once.")
        df = df[ct >= HOLDOUT_START]
    else:
        df = df[(ct >= RESEARCH[0]) & (ct < RESEARCH[1])]
    lines, b1, m1, m2, lo, hi, h1, h2, by_series = report(df, "HOLDOUT" if holdout else "research")
    if holdout:
        checks = [("Positive after costs", m1 > 0, f"{m1:+.2%} per $1 staked on {len(b1)} bets")]
    else:
        checks = [
            ("Enough bets", len(b1) >= 300, f"{len(b1)} bets (need 300)"),
            ("Profitable after costs", m1 > 0, f"{m1:+.2%} per $1 staked"),
            ("95% interval above zero", lo > 0, f"{lo:+.2%} to {hi:+.2%} (resampling events)"),
            ("Profitable at 2x costs", m2 > 0, f"{m2:+.2%} per $1 staked"),
            ("Both halves positive", h1 > 0 and h2 > 0, f"first half {h1:+.2%}, second half {h2:+.2%}"),
        ]
    ok = all(c[1] for c in checks)
    lines += ["", f"## Verdict: {'PASSED' if ok else 'FAILED'}", "", "| Check | Result | Detail |", "|---|---|---|",
              *[f"| {n} | {'PASS' if p else 'FAIL'} | {d} |" for n, p, d in checks],
              "", f"Win rate on favourites: {b1.won.mean():.1%} at an average price of {b1.price.mean():.1%} "
              f"(cost incl. fees/slippage {b1.cost.mean():.1%}).",
              "", "## Largest series", "| Series | Bets | Profit per $1 |", "|---|---|---|",
              *[f"| {s} | {int(r['count'])} | {r['mean']:+.1%} |" for s, r in by_series.head(12).iterrows()]]
    text = "\n".join(lines) + "\n"
    print(text)
    out = OUT.parent.parent / "reports" / f"kalshi-{'holdout' if holdout else 'research'}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    if holdout:
        with log.open("a") as f:
            f.write(f"| {datetime.now(timezone.utc):%Y-%m-%d} | `kalshi-favourite` | buy favourite >= {FAV:.0%}, "
                    f"3h before close | {m1:+.2%} per $1 | - | {'PASS' if ok else 'FAIL'} |\n")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "research"
    if cmd == "collect":
        collect()
    elif cmd == "holdout":
        sys.exit(0 if research(holdout=True) else 2)
    else:
        sys.exit(0 if research() else 2)

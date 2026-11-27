"""Funding carry: long spot + short perpetual on the same coin, collecting funding.

Research only (no live execution yet). Rules are pre-registered in learning.md.

Daily P&L per $1 hedged notional:
    funding received that day (positive rate = shorts are paid)
  + spot return - perp return  (captures basis changes; price moves cancel)
Returns are expressed per $ of capital, where each $1 hedged needs
$1 of spot plus `margin` of perp collateral.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from .backtest import buy_and_hold, deflated_sharpe, metrics, monte_carlo_drawdown
from .config import Config
from .data import _fname, load_panel
from .research import _fmt, all_trials, log_trial, walk_forward

ARCHIVE = "https://data.binance.vision/data/futures/um/monthly"
DAYS = 365


@dataclass(frozen=True)
class CarryParams:
    entry_apr: float = 0.10       # enter when trailing funding is above this, annualised
    exit_apr: float = 0.0         # exit when trailing funding falls below this
    lookback: int = 7             # days of funding averaged for the signal
    max_weight: float = 0.25      # max share of capital per coin
    margin: float = 0.5           # perp collateral per $1 hedged
    rebalance_weekday: int = 0
    liq_move: float = 0.40        # rally since last top-up that force-closes the short
    liq_penalty: float = 0.01     # extra slippage on a forced close
    spot_cost: float = 0.0015     # per side, fee + slippage
    perp_cost: float = 0.0010     # per side, fee + slippage

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        unknown = set(d) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown carry settings: {sorted(unknown)}")
        return cls(**d)

    def to_dict(self):
        return {"kind": "carry", **asdict(self)}


@dataclass
class CarryResult:
    returns: pd.Series
    gross: pd.Series
    entries: int
    forced: int
    funding_income: float
    basis_pnl: float
    cost_paid: float


# ---------------------------------------------------------------- data

def _month_range(start: pd.Timestamp, end: pd.Timestamp):
    m = pd.Timestamp(start.year, start.month, 1)
    while m <= end:
        yield m
        m = (m + pd.offsets.MonthBegin(1))


def _get_zip_csv(url: str) -> pd.DataFrame | None:
    r = requests.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        raw = z.read(name).decode()
    first = raw.split("\n", 1)[0]
    header = 0 if any(c.isalpha() for c in first) else None
    return pd.read_csv(io.StringIO(raw), header=header)


def download(cfg: Config) -> None:
    start = max(cfg.research_start, pd.Timestamp("2019-09-01"))
    last_full = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize().replace(day=1) - pd.Timedelta(days=1)
    log = []
    from concurrent.futures import ThreadPoolExecutor

    months = [f"{m.year}-{m.month:02d}" for m in _month_range(start, last_full)]
    for sym in cfg.universe:
        pair = sym.replace("/", "")
        fpath = cfg.workdir / "data" / "carry" / "funding" / f"{_fname(sym)}.csv"
        kpath = cfg.workdir / "data" / "carry" / "perp" / f"{_fname(sym)}.csv"
        if fpath.exists() and kpath.exists():
            print(f"| {sym} | already downloaded |", flush=True)
            continue
        urls = [f"{ARCHIVE}/fundingRate/{pair}/{pair}-fundingRate-{t}.zip" for t in months] + \
               [f"{ARCHIVE}/klines/{pair}/1d/{pair}-1d-{t}.zip" for t in months]
        with ThreadPoolExecutor(max_workers=8) as pool:
            got = list(pool.map(_get_zip_csv, urls))
        fund, perp = [], []
        for f in got[: len(months)]:
            if f is not None:
                f = f.iloc[:, [0, 2]] if f.shape[1] >= 3 else f.iloc[:, [0, 1]]
                f.columns = ["time", "rate"]
                fund.append(f)
        for k in got[len(months):]:
            if k is not None:
                k = k.iloc[:, :6]
                k.columns = ["timestamp", "open", "high", "low", "close", "volume"]
                perp.append(k)
        fdf = pd.concat(fund).drop_duplicates("time").sort_values("time") if fund else pd.DataFrame(columns=["time", "rate"])
        kdf = pd.concat(perp).drop_duplicates("timestamp").sort_values("timestamp") if perp else pd.DataFrame()
        fdf.to_csv(cfg.p("data", "carry", "funding", f"{_fname(sym)}.csv"), index=False)
        kdf.to_csv(cfg.p("data", "carry", "perp", f"{_fname(sym)}.csv"), index=False)
        line = (f"| {sym} | binance futures archive | {len(fdf)} funding prints, {len(kdf)} perp bars | "
                f"downloaded {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC |")
        log.append(line)
        print(line, flush=True)
    with cfg.p("data", "sources.md").open("a") as fh:
        fh.write("\n".join(log) + "\n")


def load(cfg: Config, end: pd.Timestamp | None = None):
    """Daily panels: funding (sum of that UTC day's rates), spot close, perp close/high."""
    spot = load_panel(cfg, end=end)
    idx = spot["close"].index
    fund, pclose, phigh = {}, {}, {}
    for sym in cfg.universe:
        f = pd.read_csv(cfg.workdir / "data" / "carry" / "funding" / f"{_fname(sym)}.csv")
        k = pd.read_csv(cfg.workdir / "data" / "carry" / "perp" / f"{_fname(sym)}.csv")
        if len(f):
            f["date"] = pd.to_datetime(f["time"].astype("int64"), unit="ms").dt.normalize()
            fund[sym] = f.groupby("date")["rate"].sum().astype(float)
        else:
            fund[sym] = pd.Series(dtype=float)
        if len(k):
            ts = k["timestamp"].astype("int64")
            ts = np.where(ts > 1e14, ts // 1000, ts)  # some archives use microseconds
            k.index = pd.to_datetime(ts, unit="ms").normalize()
            k = k[~k.index.duplicated()]
            pclose[sym], phigh[sym] = k["close"].astype(float), k["high"].astype(float)
        else:
            pclose[sym] = phigh[sym] = pd.Series(dtype=float)
    F = pd.DataFrame(fund).reindex(idx)
    PC = pd.DataFrame(pclose).reindex(idx)
    PH = pd.DataFrame(phigh).reindex(idx)
    # A day only counts if spot, perp and funding all exist: never fill gaps.
    F = F.where(PC.notna() & spot["close"].notna())
    return {"funding": F, "spot": spot["close"], "perp": PC, "perp_high": PH}


# ---------------------------------------------------------------- simulation

def signal(F: pd.DataFrame, p: CarryParams) -> pd.DataFrame:
    """Hysteresis: on above entry_apr, off below exit_apr, using closed days only."""
    apr = F.rolling(p.lookback, min_periods=p.lookback).mean() * DAYS  # F = daily sum of rates
    A = apr.to_numpy()
    out = np.zeros_like(A, dtype=bool)
    state = np.zeros(A.shape[1], dtype=bool)
    for t in range(len(A)):
        a = A[t]
        on = ~np.isnan(a) & (a > p.entry_apr)
        off = np.isnan(a) | (a < p.exit_apr)
        state = np.where(state, ~off, on)
        out[t] = state
    return pd.DataFrame(out, index=F.index, columns=F.columns)


def simulate(data, p: CarryParams, cost_mult: float = 1.0, sig: pd.DataFrame | None = None) -> CarryResult:
    F = data["funding"].to_numpy()
    S = data["spot"].to_numpy()
    P = data["perp"].to_numpy()
    PH = data["perp_high"].to_numpy()
    idx = data["funding"].index
    if sig is None:
        sig = signal(data["funding"], p)
    SG = sig.to_numpy()
    weekday = idx.weekday.to_numpy()
    T, N = F.shape
    leg = (p.spot_cost + p.perp_cost) * cost_mult      # cost per $ notional per side
    cap_per_notional = 1.0 + p.margin

    n = np.zeros(N)            # $ notional hedged per $ capital
    ref = np.full(N, np.nan)   # perp price at last margin top-up
    rets = np.zeros(T)
    gross = np.zeros(T)
    entries = forced = 0
    fund_inc = basis = costs = 0.0
    for t in range(T):
        day = 0.0
        if t > 0:
            held = n > 0
            if held.any():
                f = np.nan_to_num(F[t], nan=0.0)
                rs = S[t] / S[t - 1] - 1
                rp = P[t] / P[t - 1] - 1
                b = np.nan_to_num(rs - rp, nan=0.0)
                fi = float(n[held] @ f[held])
                bi = float(n[held] @ b[held])
                day += fi + bi
                fund_inc += fi
                basis += bi
                # forced close if the short's margin is blown by a rally
                with np.errstate(invalid="ignore", divide="ignore"):
                    blown = held & (PH[t] / ref - 1 > p.liq_move)
                if blown.any():
                    c = float(n[blown].sum()) * (leg + p.liq_penalty)
                    day -= c
                    costs += c
                    forced += int(blown.sum())
                    n[blown] = 0.0
                    ref[blown] = np.nan
        # decide at close t
        tradable = ~np.isnan(S[t]) & ~np.isnan(P[t])
        on = SG[t] & tradable
        k = on.sum()
        target = np.zeros(N)
        if k:
            per = min(p.max_weight, 1.0 / k) / cap_per_notional  # notional per $ capital
            target[on] = per
        reb = weekday[t] == p.rebalance_weekday
        new = n.copy()
        new[~on & tradable] = 0.0                      # exits any day
        if reb:
            new[on] = target[on]                        # entries/resizes on rebalance day
            ref = np.where(new > 0, P[t], np.nan)       # weekly margin top-up
        ent = (n <= 0) & (new > 0)
        ref = np.where(ent, P[t], ref)
        entries += int(ent.sum())
        c = float(np.abs(new - n).sum()) * leg
        day -= c
        costs += c
        n = new
        rets[t] = day
        gross[t] = float(n.sum() * cap_per_notional)
    return CarryResult(pd.Series(rets, idx), pd.Series(gross, idx), entries, forced,
                       fund_inc, basis, costs)


def random_sig(sig: pd.DataFrame, valid: pd.DataFrame, frac: float, p: CarryParams, rng) -> pd.DataFrame:
    frac = min(max(frac, 0.02), 0.98)
    off_p = 0.25
    on_p = frac * off_p / (1 - frac)
    reb = sig.index.weekday.to_numpy() == p.rebalance_weekday
    T, N = sig.shape
    st = rng.random(N) < frac
    out = np.zeros((T, N), dtype=bool)
    for t in range(T):
        if reb[t]:
            u = rng.random(N)
            st = np.where(st, u >= off_p, u < on_p)
        out[t] = st
    return pd.DataFrame(out, index=sig.index, columns=sig.columns) & valid


# ---------------------------------------------------------------- research

def _hash(p: CarryParams, cfg: Config) -> str:
    blob = json.dumps({"p": p.to_dict(), "u": cfg.universe}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _label(p: CarryParams) -> str:
    return f"entry={p.entry_apr:.0%} exit={p.exit_apr:.0%} lookback={p.lookback}d"


def _log(cfg, kind, p, res, mult, m):
    log_trial(cfg, kind, p, res, mult, m)


def research(cfg: Config, n_random: int = 100) -> tuple[str, bool]:
    p = CarryParams.from_dict(cfg.raw.get("carry"))
    grid = cfg.raw.get("carry_grid", {}) or {}
    g = cfg.gates
    kill = cfg.risk["kill_switch_drawdown"]
    data = load(cfg, end=cfg.holdout_start)
    first = data["funding"].dropna(how="all").index.min()
    data = {k: v[v.index >= first] for k, v in data.items()}

    r1, r2 = simulate(data, p, 1.0), simulate(data, p, 2.0)
    m1, m2 = metrics(r1.returns), metrics(r2.returns)
    _log(cfg, "carry-candidate", p, r1, 1, m1)
    _log(cfg, "carry-candidate", p, r2, 2, m2)

    import itertools
    keys = list(grid)
    variants = [replace(p, **dict(zip(keys, c))) for c in itertools.product(*(grid[k] for k in keys))] or [p]
    by = {}
    rows = []
    for v in variants:
        r = r1 if v == p else simulate(data, v, 1.0)
        m = metrics(r.returns)
        if v != p:
            _log(cfg, "carry-grid", v, r, 1, m)
        by[v] = r.returns
        rows.append((v, m, r))

    nbs = []
    for f in (0.75, 1.25):
        nbs.append(replace(p, entry_apr=round(p.entry_apr * f, 4)))
        nbs.append(replace(p, lookback=max(2, int(round(p.lookback * f)))))
        nbs.append(replace(p, liq_move=round(p.liq_move * f, 3)))
    nb_rows = []
    for v in nbs:
        r = simulate(data, v, 1.0)
        m = metrics(r.returns)
        _log(cfg, "carry-neighbour", v, r, 1, m)
        nb_rows.append((v, m))

    windows, oos = walk_forward(by, label=_label)
    pos = float(np.mean([w["return"] > 0 for w in windows])) if windows else 0.0
    m_oos = metrics(oos)

    t = all_trials(cfg)
    t1 = t[t.cost_mult.astype(float) == 1]
    n_trials = t1["hash"].nunique()
    dsr = deflated_sharpe(r1.returns, t1["daily_sr"].astype(float).tolist(), n_trials)
    mc = monte_carlo_drawdown(r1.returns)

    bench = metrics(buy_and_hold(data["spot"][cfg.benchmark]))
    sig = signal(data["funding"], p)
    valid = data["funding"].notna() & data["perp"].notna()
    frac = float(sig.to_numpy().sum() / max(valid.to_numpy().sum(), 1))
    rng = np.random.default_rng(42)
    rand = [metrics(simulate(data, p, 1.0, sig=random_sig(sig, valid, frac, p, rng)).returns)["sharpe"]
            for _ in range(n_random)]
    cut = float(np.percentile(rand, g["random_percentile"]))
    rank = float((np.array(rand) < m1["sharpe"]).mean() * 100)
    always = metrics(simulate(data, p, 1.0, sig=valid).returns)

    checks = [
        ("Enough trades", r1.entries >= g["min_trades"], f"{r1.entries} entries (need {g['min_trades']})"),
        ("Profitable after real costs", m1["cagr"] > 0, _fmt(m1)),
        ("Profitable at 2x costs", m2["cagr"] > 0, _fmt(m2)),
        ("Walk-forward", pos >= g["walk_forward_min_positive"],
         f"{pos:.0%} of {len(windows)} test quarters positive; stitched {_fmt(m_oos)}"),
        ("No cliff (neighbours profitable)", all(m["cagr"] > 0 for _, m in nb_rows),
         ", ".join(f"{_label(v)} liq={v.liq_move:.0%}: {m['cagr']:+.1%}" for v, m in nb_rows)),
        ("Luck check: deflated Sharpe", dsr >= g["min_deflated_sharpe"],
         f"{dsr:.2f} (need {g['min_deflated_sharpe']}), {n_trials} variants tried all-time"),
        ("Luck check: Monte Carlo drawdown", mc > -kill, f"worst-5% drawdown {mc:.1%} vs kill switch -{kill:.0%}"),
        ("Beats buy-and-hold on ret/DD", m1["ret_dd"] > bench["ret_dd"],
         f"strategy {m1['ret_dd']:.2f} vs {cfg.benchmark} {bench['ret_dd']:.2f}"),
        ("Beats random entries", m1["sharpe"] > cut,
         f"Sharpe {m1['sharpe']:.2f} beats {rank:.0f}% of {n_random} random runs "
         f"(need >{g['random_percentile']}th pct = {cut:.2f})"),
    ]
    passed = all(ok for _, ok, _ in checks)
    yrs = len(r1.returns) / DAYS
    lines = [
        f"# Carry research report: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
        f"Period: {r1.returns.index[0].date()} → {r1.returns.index[-1].date()} (holdout from "
        f"{cfg.holdout_start.date()} not touched). Universe: {len(cfg.universe)} coins. Test ID: `{_hash(p, cfg)}`.",
        "", f"Settings: `{json.dumps(p.to_dict())}`", "",
        "## Verdict: " + ("PASSED research gates. Freeze it and run the holdout once."
                          if passed else "FAILED. Do not trade this."), "",
        "| Check | Result | Detail |", "|---|---|---|",
        *[f"| {n} | {'PASS' if ok else 'FAIL'} | {d} |" for n, ok, d in checks], "",
        "## Numbers",
        f"- Strategy (real costs): {_fmt(m1)}",
        f"- Strategy (2x costs): {_fmt(m2)}",
        f"- Always-on carry, same sizing (no timing): {_fmt(always)}",
        f"- {cfg.benchmark} buy-and-hold: {_fmt(bench)}",
        f"- Per year, as % of capital: funding {r1.funding_income / yrs:+.1%}, basis {r1.basis_pnl / yrs:+.1%}, "
        f"costs {-r1.cost_paid / yrs:.1%}",
        f"- Entries {r1.entries}, forced margin closes {r1.forced}, avg capital in use {r1.gross.mean():.0%}, "
        f"time in trades {frac:.0%} of coin-days",
        "", "## Grid (all logged to trials.csv)", "| Settings | Result | Entries |", "|---|---|---|",
        *[f"| {_label(v)} | {_fmt(m)} | {r.entries} |" for v, m, r in rows], "",
        "## Walk-forward windows", "| Test start | Chosen settings | Quarter return |", "|---|---|---|",
        *[f"| {w['test_start']} | {w['settings']} | {w['return']:+.1%} |" for w in windows], "",
        "Not modelled: exchange failure or frozen withdrawals, funding rule changes. "
        "None of this is a guarantee.",
    ]
    text = "\n".join(lines) + "\n"
    cfg.p("reports", f"carry-research-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md").write_text(text)
    return text, passed

"""Research report on the research period, and the one-time holdout test."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .backtest import (buy_and_hold, deflated_sharpe, metrics, monte_carlo_drawdown,
                       random_signal, simulate)
from .config import Config
from .data import load_panel
from .strategy import Params, weights_from_signal

TRIAL_FIELDS = ["time", "kind", "hash", "params", "start", "end", "cost_mult",
                "sharpe", "daily_sr", "cagr", "max_dd", "entries"]


def param_hash(p: Params, cfg: Config) -> str:
    blob = json.dumps({"p": p.to_dict(), "u": cfg.universe, "c": cfg.cost_per_side}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- trial ledger

def log_trial(cfg: Config, kind: str, p: Params, res, cost_mult: float, m: dict) -> None:
    path = cfg.p("trials.csv")
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, TRIAL_FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
            "hash": param_hash(p, cfg), "params": json.dumps(p.to_dict(), sort_keys=True),
            "start": str(res.returns.index[0].date()), "end": str(res.returns.index[-1].date()),
            "cost_mult": cost_mult, "sharpe": round(m["sharpe"], 4), "daily_sr": m["daily_sr"],
            "cagr": round(m["cagr"], 4), "max_dd": round(m["max_dd"], 4), "entries": res.entries,
        })


def all_trials(cfg: Config) -> pd.DataFrame:
    path = cfg.workdir / "trials.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame(columns=TRIAL_FIELDS)


# ---------------------------------------------------------------- helpers

def grid_params(base: Params, grid: dict) -> list[Params]:
    if not grid:
        return [base]
    keys = list(grid)
    return [replace(base, **dict(zip(keys, combo)))
            for combo in itertools.product(*(grid[k] for k in keys))]


def neighbours(p: Params) -> list[Params]:
    out = []
    for f in (0.75, 1.25):
        out.append(replace(p, sma=max(5, int(round(p.sma * f)))))
        out.append(replace(p, vol_lookback=max(10, int(round(p.vol_lookback * f)))))
        out.append(replace(p, stop_mult=round(p.stop_mult * f, 2)))
    return out


def walk_forward(returns_by_param: dict, train_days=365, test_days=90, label=None):
    """Pick the best param on each trailing year, then record its next quarter."""
    any_r = next(iter(returns_by_param.values()))
    idx = any_r.index
    start = idx[0] + pd.Timedelta(days=train_days)
    windows = []
    oos = []
    while start + pd.Timedelta(days=test_days) <= idx[-1] + pd.Timedelta(days=1):
        tr = (idx >= start - pd.Timedelta(days=train_days)) & (idx < start)
        te = (idx >= start) & (idx < start + pd.Timedelta(days=test_days))
        best = max(returns_by_param, key=lambda p: metrics(returns_by_param[p][tr])["sharpe"])
        chunk = returns_by_param[best][te]
        windows.append({"test_start": str(start.date()), "settings": (label or _short)(best),
                        "return": float((1 + chunk).prod() - 1)})
        oos.append(chunk)
        start += pd.Timedelta(days=test_days)
    stitched = pd.concat(oos) if oos else pd.Series(dtype=float)
    return windows, stitched


def _fmt(m: dict) -> str:
    return (f"CAGR {m['cagr']:+.1%} | vol {m['vol']:.1%} | Sharpe {m['sharpe']:.2f} | "
            f"max DD {m['max_dd']:.1%} | ret/DD {m['ret_dd']:.2f}")


# ---------------------------------------------------------------- research

def research(cfg: Config, n_random: int = 100, panel: dict | None = None) -> tuple[str, bool]:
    panel = panel or load_panel(cfg, end=cfg.holdout_start)
    close = panel["close"]
    cost = cfg.cost_per_side
    p = cfg.strategy
    g = cfg.gates
    kill = cfg.risk["kill_switch_drawdown"]

    # candidate at real and doubled costs
    res1 = simulate(panel, p, cost, delist_haircut=cfg.delist_haircut)
    res2 = simulate(panel, p, 2 * cost, delist_haircut=cfg.delist_haircut)
    m1, m2 = metrics(res1.returns), metrics(res2.returns)
    log_trial(cfg, "candidate", p, res1, 1, m1)
    log_trial(cfg, "candidate", p, res2, 2, m2)

    # grid (every variant is logged as a trial)
    grid = grid_params(p, cfg.grid)
    by_param: dict[Params, pd.Series] = {}
    grid_rows = []
    for gp in grid:
        r = res1 if gp == p else simulate(panel, gp, cost, delist_haircut=cfg.delist_haircut)
        m = metrics(r.returns)
        if gp != p:
            log_trial(cfg, "grid", gp, r, 1, m)
        by_param[gp] = r.returns
        grid_rows.append((gp, m))

    # neighbours: no cliff around the chosen settings
    nb_rows = []
    for nbp in neighbours(p):
        r = simulate(panel, nbp, cost, delist_haircut=cfg.delist_haircut)
        m = metrics(r.returns)
        log_trial(cfg, "neighbour", nbp, r, 1, m)
        nb_rows.append((nbp, m))

    # walk-forward across the grid
    windows, oos = walk_forward(by_param)
    pos_share = np.mean([w["return"] > 0 for w in windows]) if windows else 0.0
    m_oos = metrics(oos)

    # luck checks
    trials = all_trials(cfg)
    t1 = trials[trials.cost_mult.astype(float) == 1]
    n_trials = t1["hash"].nunique()
    dsr = deflated_sharpe(res1.returns, t1["daily_sr"].astype(float).tolist(), n_trials)
    mc_dd = monte_carlo_drawdown(res1.returns)

    # baselines
    bench = metrics(buy_and_hold(close[cfg.benchmark]))
    on_frac = float((res1.gross > 0).mean()) if len(res1.gross) else 0.5
    sig_frac = float(np.clip((weights_from_signal_frac(close, p)), 0.05, 0.95))
    rng = np.random.default_rng(42)
    rand_sh = []
    for _ in range(n_random):
        sig = random_signal(close, sig_frac, p, rng)
        tgt = weights_from_signal(close, sig, p)
        rand_sh.append(metrics(simulate(panel, p, cost, target=tgt,
                                        delist_haircut=cfg.delist_haircut).returns)["sharpe"])
    rand_cut = float(np.percentile(rand_sh, g["random_percentile"])) if rand_sh else 0.0
    rand_rank = float((np.array(rand_sh) < m1["sharpe"]).mean() * 100) if rand_sh else 0.0

    checks = [
        ("Enough trades", res1.entries >= g["min_trades"],
         f"{res1.entries} entries (need {g['min_trades']})"),
        ("Profitable after real costs", m1["cagr"] > 0, _fmt(m1)),
        ("Profitable at 2x costs", m2["cagr"] > 0, _fmt(m2)),
        ("Walk-forward", pos_share >= g["walk_forward_min_positive"],
         f"{pos_share:.0%} of {len(windows)} test quarters positive; stitched {_fmt(m_oos)}"),
        ("No cliff (neighbours profitable)", all(m["cagr"] > 0 for _, m in nb_rows),
         ", ".join(f"{_short(q)}: {m['cagr']:+.1%}" for q, m in nb_rows)),
        ("Luck check: deflated Sharpe", dsr >= g["min_deflated_sharpe"],
         f"{dsr:.2f} (need {g['min_deflated_sharpe']}), {n_trials} variants tried all-time"),
        ("Luck check: Monte Carlo drawdown", mc_dd > -kill,
         f"worst-5% drawdown {mc_dd:.1%} vs kill switch -{kill:.0%}"),
        ("Beats buy-and-hold on ret/DD", m1["ret_dd"] > bench["ret_dd"],
         f"strategy {m1['ret_dd']:.2f} vs {cfg.benchmark} {bench['ret_dd']:.2f}"),
        ("Beats random entries", m1["sharpe"] > rand_cut,
         f"Sharpe {m1['sharpe']:.2f} beats {rand_rank:.0f}% of {n_random} random runs "
         f"(need >{g['random_percentile']}th pct = {rand_cut:.2f})"),
    ]
    passed = all(ok for _, ok, _ in checks)

    lines = [
        f"# Research report: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "",
        f"Period: {close.index[0].date()} → {close.index[-1].date()} (holdout from "
        f"{cfg.holdout_start.date()} not touched). Universe: {len(cfg.universe)} coins. "
        f"Costs: {cost:.2%} per side. Test ID: `{param_hash(p, cfg)}`.",
        "",
        f"Candidate settings: `{json.dumps(p.to_dict())}`",
        "",
        "## Verdict: " + ("PASSED research gates. Freeze it and run the holdout once."
                          if passed else "FAILED. Do not trade this."),
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
        *[f"| {n} | {'PASS' if ok else 'FAIL'} | {d} |" for n, ok, d in checks],
        "",
        "## Numbers",
        f"- Strategy (real costs): {_fmt(m1)}",
        f"- Strategy (2x costs): {_fmt(m2)}",
        f"- {cfg.benchmark} buy-and-hold: {_fmt(bench)}",
        f"- Entries {res1.entries}, exits {res1.exits}, stop-outs {res1.stop_hits}, "
        f"avg exposure {res1.gross.mean():.0%}, time with any position {on_frac:.0%}, "
        f"turnover {res1.turnover.sum() / max(len(close) / 365, 1e-9):.1f}x equity per year",
        "",
        "## Grid (all logged to trials.csv)",
        "| Settings | Result |", "|---|---|",
        *[f"| {_short(q)} | {_fmt(m)} |" for q, m in grid_rows],
        "",
        "## Walk-forward windows",
        "| Test start | Chosen settings | Quarter return |", "|---|---|---|",
        *[f"| {w['test_start']} | {w['settings']} | {w['return']:+.1%} |" for w in windows],
        "",
        "Worst case is shown next to best on purpose. None of this is a guarantee.",
    ]
    text = "\n".join(lines) + "\n"
    out = cfg.p("reports", f"research-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md")
    out.write_text(text)
    return text, passed


def weights_from_signal_frac(close: pd.DataFrame, p: Params) -> float:
    from .strategy import trend_signal
    sig = trend_signal(close, p)
    avail = close.notna()
    return float(sig.to_numpy().sum() / max(avail.to_numpy().sum(), 1))


def _short(p: Params) -> str:
    return f"sma={p.sma} vol={p.vol_lookback} stop={p.stop_mult} top_k={p.top_k}"


# ---------------------------------------------------------------- holdout

def holdout(cfg: Config, panel: dict | None = None) -> tuple[str, bool]:
    p = cfg.strategy
    h = param_hash(p, cfg)
    ledger = cfg.p("holdout_log.md")
    if ledger.exists() and f"`{h}`" in ledger.read_text():
        raise RuntimeError(
            f"Settings {h} were already tested on the holdout. The holdout is used once per "
            "strategy. Change the strategy (and log why in learning.md) or accept the result.")
    panel = panel or load_panel(cfg)
    close = panel["close"]
    if close.index[-1] < cfg.holdout_start + pd.Timedelta(days=180):
        raise RuntimeError("Less than 180 days of holdout data. Download more first.")
    res1 = simulate(panel, p, cfg.cost_per_side, delist_haircut=cfg.delist_haircut)
    res2 = simulate(panel, p, 2 * cfg.cost_per_side, delist_haircut=cfg.delist_haircut)
    mask = res1.returns.index >= cfg.holdout_start
    m1, m2 = metrics(res1.returns[mask]), metrics(res2.returns[mask])
    bench = metrics(buy_and_hold(close[cfg.benchmark])[mask])
    kill = cfg.risk["kill_switch_drawdown"]
    passed = m2["total"] > 0 and m1["max_dd"] > -kill
    if not ledger.exists():
        ledger.write_text("# Holdout log (append-only)\n\n| Date | Test ID | Settings | Real costs | 2x costs | Verdict |\n|---|---|---|---|---|---|\n")
    with ledger.open("a") as f:
        f.write(f"| {datetime.now(timezone.utc):%Y-%m-%d} | `{h}` | `{json.dumps(p.to_dict())}` | "
                f"{_fmt(m1)} | {_fmt(m2)} | {'PASS' if passed else 'FAIL'} |\n")
    text = (f"# Holdout test `{h}` ({cfg.holdout_start.date()} → {close.index[-1].date()})\n\n"
            f"- Real costs: {_fmt(m1)}\n- 2x costs: {_fmt(m2)}\n"
            f"- {cfg.benchmark} buy-and-hold: {_fmt(bench)}\n\n"
            f"Verdict: {'PASS. Next step is 3 months / 50 trades of paper trading.' if passed else 'FAIL. Move it to graveyard.md.'}\n")
    return text, passed

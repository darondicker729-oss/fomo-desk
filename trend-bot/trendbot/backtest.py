"""Daily backtest that mirrors the live rules, plus the statistics used to judge it."""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

from .strategy import DAYS_PER_YEAR, Params, daily_vol, decide, stop_level, target_weights

Panel = dict[str, pd.DataFrame]  # keys: open, high, low, close


@dataclass
class Result:
    equity: pd.Series
    returns: pd.Series
    turnover: pd.Series
    gross: pd.Series
    entries: int
    exits: int
    stop_hits: int


def simulate(panel: Panel, p: Params, cost_per_side: float,
             target: pd.DataFrame | None = None, use_stops: bool = True,
             delist_haircut: float = 0.3) -> Result:
    """Walk forward one day at a time.

    * Decisions are made at each day's close and held from the next bar.
    * Stops are checked against the next day's low; fills at min(open, stop).
    * Costs (fee + slippage per side) are charged on every unit traded.
    * Missing bars are never filled: the position just can't trade that day.
    * A coin whose data ends before the dataset does is treated as delisted
      and exited at its last close minus `delist_haircut` (a stated assumption).
    """
    close = panel["close"]
    if target is None:
        target = target_weights(close, p)
    C = close.to_numpy()
    O = panel["open"].reindex_like(close).to_numpy()
    Lo = panel["low"].reindex_like(close).to_numpy()
    TW = target.reindex_like(close).fillna(0.0).to_numpy()
    DV = daily_vol(close, p).to_numpy()
    weekday = close.index.weekday.to_numpy()
    T, N = C.shape

    valid = ~np.isnan(C)
    last_valid = np.array([np.flatnonzero(valid[:, i])[-1] if valid[:, i].any() else -1
                           for i in range(N)])
    delisted_at = np.where(last_valid < T - 1, last_valid, -1)

    w = np.zeros(N)
    stop = np.full(N, np.nan)
    equity = 1.0
    eq = np.empty(T)
    rets = np.zeros(T)
    turn = np.zeros(T)
    gross = np.zeros(T)
    entries = exits = stop_hits = 0

    for t in range(T):
        start_equity = equity
        traded = 0.0
        if t > 0:
            prev = C[t - 1]
            r = C[t] / prev - 1.0
            if use_stops:
                held = w > 0
                with np.errstate(invalid="ignore"):
                    hit = held & ~np.isnan(stop) & (Lo[t] <= stop)
                if hit.any():
                    o = np.where(np.isnan(O[t]), stop, O[t])
                    exit_px = np.minimum(o, stop)
                    r = np.where(hit, exit_px / prev - 1.0, r)
            else:
                hit = np.zeros(N, dtype=bool)
            r = np.nan_to_num(r, nan=0.0)
            g = 1.0 + float(w @ r)
            equity *= g
            w = w * (1.0 + r) / g if g > 0 else np.zeros(N)
            if hit.any():
                traded += w[hit].sum()
                stop_hits += int(hit.sum())
                exits += int(hit.sum())
                w[hit] = 0.0
                stop[hit] = np.nan

        # forced exit for coins that stop trading (delisted)
        dl = (delisted_at == t) & (w > 0)
        if dl.any():
            equity *= 1.0 - float(w[dl].sum()) * delist_haircut
            exits += int(dl.sum())
            w[dl] = 0.0
            stop[dl] = np.nan

        tradable = ~np.isnan(C[t])
        new = decide(w, TW[t], weekday[t] == p.rebalance_weekday, p, tradable)
        delta = new - w
        traded += float(np.abs(delta).sum())
        entries += int(((w <= 0) & (new > 0)).sum())
        exits += int(((w > 0) & (new <= 0)).sum())
        w = new
        equity *= 1.0 - traded * cost_per_side

        if use_stops:
            s = stop_level(C[t], DV[t], p)
            stop = np.where(w > 0, np.fmax(stop, s), np.nan)

        eq[t] = equity
        rets[t] = equity / start_equity - 1.0
        turn[t] = traded
        gross[t] = w.sum()

    idx = close.index
    return Result(pd.Series(eq, idx), pd.Series(rets, idx), pd.Series(turn, idx),
                  pd.Series(gross, idx), entries, exits, stop_hits)


# ---------------------------------------------------------------- metrics

def max_drawdown(equity: pd.Series | np.ndarray) -> float:
    e = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(e)
    return float((e / peak - 1.0).min()) if len(e) else 0.0


def metrics(returns: pd.Series) -> dict:
    r = returns.dropna()
    n = len(r)
    if n < 2:
        return {"days": n, "cagr": 0.0, "vol": 0.0, "sharpe": 0.0, "max_dd": 0.0,
                "ret_dd": 0.0, "total": 0.0, "daily_sr": 0.0}
    eq = (1 + r).cumprod()
    total = float(eq.iloc[-1] - 1)
    cagr = float(eq.iloc[-1] ** (DAYS_PER_YEAR / n) - 1) if eq.iloc[-1] > 0 else -1.0
    sd = float(r.std())
    daily_sr = float(r.mean() / sd) if sd > 0 else 0.0
    mdd = max_drawdown(eq.to_numpy())
    return {
        "days": n,
        "total": total,
        "cagr": cagr,
        "vol": sd * math.sqrt(DAYS_PER_YEAR),
        "sharpe": daily_sr * math.sqrt(DAYS_PER_YEAR),
        "daily_sr": daily_sr,
        "max_dd": mdd,
        "ret_dd": cagr / abs(mdd) if mdd < 0 else float("inf") if cagr > 0 else 0.0,
    }


def monte_carlo_drawdown(returns: pd.Series, sims: int = 2000, block: int = 20,
                         pct: float = 5.0, seed: int = 0) -> float:
    """Block-bootstrap the daily returns; return the drawdown that only
    `pct`% of reshuffled histories were worse than."""
    r = returns.dropna().to_numpy()
    n = len(r)
    if n < block * 2:
        return max_drawdown(np.cumprod(1 + r))
    rng = np.random.default_rng(seed)
    nblocks = math.ceil(n / block)
    dds = np.empty(sims)
    for i in range(sims):
        starts = rng.integers(0, n - block, nblocks)
        path = np.concatenate([r[s : s + block] for s in starts])[:n]
        dds[i] = max_drawdown(np.cumprod(1 + path))
    return float(np.percentile(dds, pct))


def deflated_sharpe(returns: pd.Series, trial_daily_srs: list[float], n_trials: int) -> float:
    """Probability the true Sharpe is above what the best of `n_trials` random
    strategies would show (Bailey & Lopez de Prado, 2014). Daily units."""
    r = returns.dropna()
    T = len(r)
    if T < 30 or r.std() == 0:
        return 0.0
    sr = float(r.mean() / r.std())
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3.0  # pandas gives excess kurtosis
    nd = NormalDist()
    if n_trials >= 2 and len(trial_daily_srs) >= 2:
        var_sr = float(np.var(trial_daily_srs, ddof=1))
        gamma = 0.5772156649
        sr0 = math.sqrt(var_sr) * ((1 - gamma) * nd.inv_cdf(1 - 1 / n_trials)
                                   + gamma * nd.inv_cdf(1 - 1 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-12))
    return nd.cdf((sr - sr0) * math.sqrt(T - 1) / denom)


# ---------------------------------------------------------------- baselines

def buy_and_hold(close: pd.Series) -> pd.Series:
    return close.pct_change(fill_method=None).fillna(0.0)


def random_signal(close: pd.DataFrame, on_fraction: float, p: Params,
                  rng: np.random.Generator, switch_off: float = 0.25) -> pd.DataFrame:
    """Random on/off per coin that flips only on rebalance days, with the same
    average time-in-market as the strategy. Same sizing and exits otherwise."""
    on_fraction = min(max(on_fraction, 0.01), 0.99)
    switch_on = on_fraction * switch_off / (1 - on_fraction)
    T, N = close.shape
    reb = close.index.weekday.to_numpy() == p.rebalance_weekday
    state = rng.random(N) < on_fraction
    out = np.zeros((T, N), dtype=bool)
    for t in range(T):
        if reb[t]:
            u = rng.random(N)
            state = np.where(state, u >= switch_off, u < switch_on)
        out[t] = state
    sig = pd.DataFrame(out, index=close.index, columns=close.columns)
    return sig & close.notna()

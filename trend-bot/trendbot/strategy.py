"""Trend-following strategy with volatility targeting.

One rule set, used unchanged by the backtest, paper trading and live trading:

* Signal: hold a coin only while its daily close is above its N-day average.
  Optionally keep only the top-K coins by recent momentum.
* Sizing: inverse-volatility weights, scaled so the whole portfolio targets a
  fixed annual volatility, using the coins' recent covariance.
* Caps: max weight per coin, max gross exposure (1.0 = no leverage), and a
  per-coin cap so that a stop hit costs at most `risk_per_trade` of equity.

Every value at date t uses closed bars up to and including t only.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields

import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class Params:
    sma: int = 100                # trend filter length, days
    vol_lookback: int = 30        # days used for volatility and covariance
    target_vol: float = 0.15      # annual portfolio volatility target
    max_weight: float = 0.25      # max share of equity in one coin
    max_gross: float = 1.0        # max total exposure (1.0 = no leverage)
    top_k: int | None = None      # keep only the K strongest coins (None = all)
    mom_lookback: int = 90        # momentum window for top_k ranking
    rebalance_weekday: int = 0    # 0 = Monday (UTC bar date)
    min_trade: float = 0.02       # ignore resizes smaller than this weight
    stop_mult: float = 3.0        # stop distance in daily standard deviations
    risk_per_trade: float = 0.01  # max equity lost if a stop is hit
    signal: str = "trend"         # "trend" (above SMA) or "always" (hold all, sized by vol)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Params":
        d = dict(d or {})
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"Unknown strategy settings: {sorted(unknown)}")
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)


def daily_returns(close: pd.DataFrame) -> pd.DataFrame:
    return close.pct_change(fill_method=None)


def daily_vol(close: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Rolling std of daily returns; NaN until a full window of real data."""
    return daily_returns(close).rolling(p.vol_lookback, min_periods=p.vol_lookback).std()


def trend_signal(close: pd.DataFrame, p: Params) -> pd.DataFrame:
    if p.signal == "always":
        return close.notna() & close.rolling(p.vol_lookback, min_periods=p.vol_lookback).count().ge(p.vol_lookback)
    if p.signal != "trend":
        raise ValueError(f"unknown signal {p.signal!r}")
    sma = close.rolling(p.sma, min_periods=p.sma).mean()
    sig = (close > sma) & sma.notna() & close.notna()
    if p.top_k:
        mom = close / close.shift(p.mom_lookback) - 1
        rank = mom.where(sig).rank(axis=1, ascending=False, method="first")
        sig = sig & (rank <= p.top_k)
    return sig


def weights_from_signal(close: pd.DataFrame, sig: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Turn an on/off signal into portfolio weights (shared by strategy and baselines)."""
    rets = daily_returns(close).to_numpy()
    dvol = daily_vol(close, p).to_numpy()
    active = sig.to_numpy(dtype=bool) & (np.nan_to_num(dvol, nan=0.0) > 0)
    T, N = close.shape
    out = np.zeros((T, N))
    L = p.vol_lookback
    for t in range(T):
        a = active[t]
        if not a.any() or t + 1 < L:
            continue
        window = rets[t - L + 1 : t + 1][:, a]
        if np.isnan(window).any():  # only possible with gaps; skip rather than guess
            ok = ~np.isnan(window).any(axis=0)
            idx = np.flatnonzero(a)[ok]
            a = np.zeros(N, dtype=bool)
            a[idx] = True
            if not a.any():
                continue
            window = rets[t - L + 1 : t + 1][:, a]
        raw = 1.0 / dvol[t, a]
        cov = np.atleast_2d(np.cov(window, rowvar=False))
        port_vol = math.sqrt(max(float(raw @ cov @ raw), 0.0)) * math.sqrt(DAYS_PER_YEAR)
        if port_vol <= 0:
            continue
        w = raw * (p.target_vol / port_vol)
        stop_cap = p.risk_per_trade / (p.stop_mult * dvol[t, a])
        w = np.minimum(w, np.minimum(p.max_weight, stop_cap))
        gross = w.sum()
        if gross > p.max_gross:
            w *= p.max_gross / gross
        out[t, a] = w
    return pd.DataFrame(out, index=close.index, columns=close.columns)


def target_weights(close: pd.DataFrame, p: Params) -> pd.DataFrame:
    return weights_from_signal(close, trend_signal(close, p), p)


def decide(w: np.ndarray, tgt: np.ndarray, rebalance: bool, p: Params,
           tradable: np.ndarray) -> np.ndarray:
    """Next holdings from current weights and today's targets.

    Rebalance day: move to target (skipping tiny resizes).
    Other days: only exits, for coins whose signal turned off.
    """
    if rebalance:
        new = tgt.copy()
        keep = (np.abs(new - w) < p.min_trade) & (new > 0) & (w > 0)
        new[keep] = w[keep]
    else:
        new = w.copy()
        new[tgt <= 0] = 0.0
    new[~tradable] = w[~tradable]
    return new


def stop_level(close_t: np.ndarray, dvol_t: np.ndarray, p: Params) -> np.ndarray:
    return close_t * (1.0 - p.stop_mult * dvol_t)

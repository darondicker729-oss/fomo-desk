import numpy as np
import pandas as pd
import pytest
import yaml

from trendbot import config as config_mod

DAY_MS = 86_400_000


def make_frames(n_days=900, n_coins=5, seed=0, start="2020-01-06"):
    """Correlated random walks with regime-switching drift (trending periods)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_days, freq="D")
    market = rng.normal(0, 0.03, n_days)
    drift = np.repeat(rng.choice([-0.004, 0.0, 0.004], n_days // 60 + 1), 60)[:n_days]
    frames = {}
    for i in range(n_coins):
        r = 0.7 * market + 0.3 * rng.normal(0, 0.04, n_days) + drift
        close = 100 * np.exp(np.cumsum(r))
        open_ = np.r_[close[0], close[:-1]]
        hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.01, n_days)))
        lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.01, n_days)))
        frames[f"C{i}/USDT"] = pd.DataFrame(
            {"open": open_, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)
    return frames


def to_panel(frames):
    from trendbot.data import frames_to_panel
    return frames_to_panel(frames)


@pytest.fixture
def frames():
    return make_frames()


@pytest.fixture
def cfg(tmp_path, frames):
    raw = {
        "mode": "paper", "exchange": "fake", "quote": "USDT", "data_source": "fake",
        "universe": list(frames), "benchmark": list(frames)[0],
        "research_start": "2020-01-01", "holdout_start": "2021-12-01",
        "costs": {"fee_per_side": 0.001, "slippage_per_side": 0.0005},
        "strategy": {"sma": 50, "vol_lookback": 20},
        "grid": {"sma": [30, 50, 80]},
        "risk": {"kill_switch_drawdown": 0.2, "daily_loss_limit": 0.03},
        "paper_start_cash": 10000,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return config_mod.load(path)


class FakeExchange:
    """Serves daily candles from frames, like ccxt fetch_ohlcv."""

    def __init__(self, frames):
        self.frames = frames

    def fetch_ohlcv(self, sym, tf="1d", since=None, limit=None):
        df = self.frames[sym]
        rows = []
        for ts, r in df.iterrows():
            ms = int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)
            if since is None or ms >= since:
                rows.append([ms, r.open, r.high, r.low, r.close, r.volume])
        return rows


def ms_after_bar(day):
    """A moment just after the given daily bar has closed."""
    return int(pd.Timestamp(day, tz="UTC").timestamp() * 1000) + DAY_MS + 60_000

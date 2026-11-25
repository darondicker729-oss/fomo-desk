import numpy as np
import pandas as pd
import pytest

from trendbot.backtest import deflated_sharpe, max_drawdown, metrics, simulate
from trendbot.data import check_frame, drop_unclosed
from trendbot.strategy import Params, decide, target_weights

from .conftest import make_frames, to_panel


def test_no_lookahead():
    """Changing the future must not change any past weight."""
    frames = make_frames(n_days=400)
    panel = to_panel(frames)
    p = Params(sma=50, vol_lookback=20)
    w1 = target_weights(panel["close"], p)
    cut = 300
    altered = {k: v.copy() for k, v in frames.items()}
    for v in altered.values():
        v.iloc[cut:, :4] *= 3.0
    w2 = target_weights(to_panel(altered)["close"], p)
    pd.testing.assert_frame_equal(w1.iloc[:cut], w2.iloc[:cut])


def test_simulate_no_lookahead():
    frames = make_frames(n_days=400)
    p = Params(sma=50, vol_lookback=20)
    r1 = simulate(to_panel(frames), p, 0.001).equity
    altered = {k: v.copy() for k, v in frames.items()}
    for v in altered.values():
        v.iloc[300:, :4] *= 0.3
    r2 = simulate(to_panel(altered), p, 0.001).equity
    pd.testing.assert_series_equal(r1.iloc[:300], r2.iloc[:300])


def test_weights_respect_caps():
    p = Params(sma=50, vol_lookback=20, max_weight=0.2, max_gross=1.0, target_vol=5.0)
    w = target_weights(to_panel(make_frames())["close"], p)
    assert (w.to_numpy() >= 0).all()
    assert w.max().max() <= 0.2 + 1e-12
    assert w.sum(axis=1).max() <= 1.0 + 1e-9


def test_vol_target_roughly_met():
    p = Params(sma=10, vol_lookback=30, target_vol=0.15, max_weight=1, risk_per_trade=1)
    panel = to_panel(make_frames(n_days=1500))
    res = simulate(panel, p, 0.0, use_stops=False)
    active = res.returns[res.gross > 0.05]
    realised = active.std() * np.sqrt(365)
    assert 0.05 < realised < 0.30


def test_costs_hurt():
    panel = to_panel(make_frames())
    p = Params(sma=50, vol_lookback=20)
    a = simulate(panel, p, 0.0).equity.iloc[-1]
    b = simulate(panel, p, 0.002).equity.iloc[-1]
    c = simulate(panel, p, 0.004).equity.iloc[-1]
    assert a > b > c


def test_stop_exits_at_stop_price():
    idx = pd.date_range("2021-01-04", periods=120, freq="D")  # Monday start
    close = np.linspace(100, 160, 120)
    close[-1] = 80.0  # crash on the last day
    f = pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close * 1.01,
                      "low": close * 0.99, "close": close, "volume": 1.0}, index=idx)
    f.loc[idx[-1], "open"] = close[-2]
    f.loc[idx[-1], "low"] = 79.0
    rng = np.random.default_rng(1)
    f["close"] = f["close"] * (1 + rng.normal(0, 0.005, 120))  # give it some volatility
    f.loc[idx[-1], "close"] = 80.0
    panel = to_panel({"A/USDT": f})
    p = Params(sma=20, vol_lookback=10, max_weight=1, risk_per_trade=1)
    res = simulate(panel, p, 0.0)
    assert res.stop_hits >= 1
    # loss on the crash day is limited to the stop distance, not the full 50% drop
    assert res.returns.iloc[-1] > -0.5 * res.gross.iloc[-2]


def test_decide_only_exits_off_rebalance():
    p = Params(min_trade=0.02)
    w = np.array([0.2, 0.0, 0.3])
    tgt = np.array([0.5, 0.4, 0.0])
    tr = np.array([True, True, True])
    np.testing.assert_allclose(decide(w, tgt, False, p, tr), [0.2, 0.0, 0.0])
    np.testing.assert_allclose(decide(w, tgt, True, p, tr), [0.5, 0.4, 0.0])
    np.testing.assert_allclose(decide(w, np.array([0.21, 0, 0]), True, p, tr), [0.2, 0, 0])


def test_metrics_and_drawdown():
    assert max_drawdown([1, 2, 1, 3]) == -0.5
    r = pd.Series([0.01, -0.01] * 200)
    m = metrics(r)
    assert m["days"] == 400 and m["max_dd"] < 0


def test_deflated_sharpe_drops_with_more_trials():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.001, 0.02, 1000))
    srs = list(rng.normal(0, 0.03, 50))
    few = deflated_sharpe(r, srs, 2)
    many = deflated_sharpe(r, srs, 10_000)
    assert 0 <= many < few <= 1


def test_data_checks_keep_gaps_and_drop_bad_bars():
    rows = [[0, 1, 2, 0.5, 1.5, 1], [86_400_000, 1, 2, 0.5, 1.5, 1],
            [3 * 86_400_000, 1, 0.5, 2, 1.5, 1],      # high < low: bad
            [4 * 86_400_000, 1, 2, 0.5, 1.5, 1]]
    df, issues = check_frame(pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]))
    assert df["close"].isna().sum() == 2  # missing day + removed bad bar, never filled
    assert any("bad bars" in i for i in issues) and any("missing" in i for i in issues)


def test_drop_unclosed():
    rows = [[0, 1, 1, 1, 1, 1], [86_400_000, 1, 1, 1, 1, 1]]
    assert len(drop_unclosed(rows, now_ms=86_400_000 + 1000)) == 1


def test_unknown_setting_rejected():
    with pytest.raises(ValueError):
        Params.from_dict({"smaa": 10})

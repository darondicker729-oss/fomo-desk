import numpy as np
import pandas as pd

from trendbot.carry import CarryParams, signal, simulate


def _data(days=120, rate=0.0003, n=2, rally_day=None):
    idx = pd.date_range("2022-01-03", periods=days, freq="D")  # Monday start
    rng = np.random.default_rng(0)
    price = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, (days, n)), axis=0))
    if rally_day is not None:
        price[rally_day:] *= 1.6
    cols = [f"C{i}/USDT" for i in range(n)]
    spot = pd.DataFrame(price, idx, cols)
    perp = spot * 1.0005  # tiny constant basis
    return {"funding": pd.DataFrame(rate, idx, cols), "spot": spot, "perp": perp,
            "perp_high": perp * 1.01}


def test_positive_funding_earns_and_prices_cancel():
    d = _data(rate=0.0003)  # 0.03%/day ~ 11% a year, above the 10% entry
    p = CarryParams(lookback=7, entry_apr=0.10, spot_cost=0, perp_cost=0, liq_move=10)
    r = simulate(d, p)
    assert r.entries == 2 and r.forced == 0
    assert abs(r.basis_pnl) < 1e-9  # constant basis: price moves fully hedged
    # funding earned on capital: notional 0.25/1.5 per coin * 2 coins * rate per held day
    assert r.funding_income > 0 and r.returns.sum() > 0


def test_no_entry_below_threshold_and_costs_charged():
    p = CarryParams(lookback=7, entry_apr=0.10)
    assert simulate(_data(rate=0.0001), p).entries == 0  # ~3.7% a year: stay out
    r0 = simulate(_data(), CarryParams(spot_cost=0, perp_cost=0, liq_move=10))
    r1 = simulate(_data(), CarryParams(liq_move=10))
    assert r1.cost_paid > 0 and r1.returns.sum() < r0.returns.sum()


def test_rally_forces_margin_close():
    d = _data(rally_day=40)
    r = simulate(d, CarryParams(lookback=7, liq_move=0.4))
    assert r.forced >= 1


def test_signal_hysteresis_uses_past_only():
    d = _data(days=60)
    f = d["funding"].copy()
    f.iloc[30:] = -0.001
    s = signal(f, CarryParams(lookback=7, entry_apr=0.10, exit_apr=0.0))
    assert s.iloc[20].all() and not s.iloc[40].any()
    f2 = f.copy()
    f2.iloc[45:] = 0.01  # changing the future can't change the past
    pd.testing.assert_frame_equal(signal(f2, CarryParams())[:45], signal(f, CarryParams())[:45])

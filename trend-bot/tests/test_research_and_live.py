import numpy as np
import pandas as pd
import pytest

from trendbot.live import Alerts, PaperBroker, Runner, State, monitor
from trendbot.research import holdout, research

from .conftest import FakeExchange, make_frames, ms_after_bar, to_panel


def test_research_report_and_trial_log(cfg, frames):
    panel = to_panel({k: v[v.index < cfg.holdout_start] for k, v in frames.items()})
    text, passed = research(cfg, n_random=5, panel=panel)
    assert "| Check | Result | Detail |" in text
    trials = pd.read_csv(cfg.workdir / "trials.csv")
    assert len(trials) >= 2 + 2 + 6  # candidate x2, other grid points, neighbours
    # a second run adds more trials (count never resets)
    research(cfg, n_random=2, panel=panel)
    assert len(pd.read_csv(cfg.workdir / "trials.csv")) > len(trials)


def test_research_never_sees_holdout(cfg, frames):
    """Wrecking the holdout data must not change the research result."""
    clean = to_panel({k: v[v.index < cfg.holdout_start] for k, v in frames.items()})
    t1, _ = research(cfg, n_random=2, panel=clean)
    assert str(cfg.holdout_start.date()) in t1
    assert clean["close"].index.max() < cfg.holdout_start


def test_holdout_runs_once(cfg, frames):
    panel = to_panel(frames)
    text, _ = holdout(cfg, panel=panel)
    assert "Holdout test" in text
    with pytest.raises(RuntimeError, match="already tested"):
        holdout(cfg, panel=panel)


def _runner(cfg, frames, day):
    state = State(cfg.p("state", "paper.json"))
    broker = PaperBroker(state, cfg)
    return Runner(cfg, FakeExchange(frames), broker, state, Alerts(cfg),
                  now_ms=ms_after_bar(day)), broker, state


def _uptrend_frames():
    frames = make_frames(n_days=300, seed=3)
    for v in frames.values():  # strong steady uptrend so the signal is on
        v.loc[:, ["open", "high", "low", "close"]] = (
            v[["open", "high", "low", "close"]].to_numpy()
            * np.linspace(1, 3, len(v))[:, None])
    return frames


def test_paper_run_buys_sets_stops_and_is_idempotent(cfg):
    frames = _uptrend_frames()
    idx = frames["C0/USDT"].index
    monday = [d for d in idx if d.weekday() == 0][-2]
    clipped = {k: v[v.index <= monday] for k, v in frames.items()}
    runner, broker, state = _runner(cfg, clipped, monday)
    out = runner.run_once()
    cash, qty = broker.holdings()
    assert out["rebalance"] and qty, "should buy on a rebalance day in an uptrend"
    assert set(broker.stops()) == set(qty), "every position must have a stop"
    for s in qty:
        assert broker.stops()[s] < clipped[s]["close"].iloc[-1]
    assert (cfg.workdir / "trades.csv").exists()
    assert "MODE: PAPER" in (cfg.workdir / "logs" / "alerts.log").read_text()
    # same bar again: nothing happens
    runner2, broker2, _ = _runner(cfg, clipped, monday)
    assert runner2.run_once().get("skipped")
    assert monitor(cfg, broker2, State(cfg.p("state", "paper.json")), Alerts(cfg)) == []


def test_paper_stop_hit_and_kill_switch(cfg):
    frames = _uptrend_frames()
    idx = frames["C0/USDT"].index
    monday = [d for d in idx if d.weekday() == 0][-2]
    clipped = {k: v[v.index <= monday] for k, v in frames.items()}
    runner, broker, state = _runner(cfg, clipped, monday)
    runner.run_once()
    held_before = broker.holdings()[1]
    assert held_before
    # next day: everything crashes 60%
    nxt = monday + pd.Timedelta(days=1)
    crashed = {}
    for k, v in frames.items():
        v = v[v.index <= nxt].copy()
        v.loc[nxt, ["open", "high", "low", "close"]] = v.loc[monday, "close"] * np.array([1.0, 1.0, 0.4, 0.4])
        crashed[k] = v
    runner2, broker2, state2 = _runner(cfg, crashed, nxt)
    out = runner2.run_once()
    fills = out.get("fills", [])
    assert not out.get("killed")
    assert sum("stop hit" in f["reason"] for f in fills) == len(held_before)
    # stopped out at the stop price, not at the 60% crash
    eq = out["equity"]
    assert eq > 10000 * 0.95, f"stops should cap the loss, equity {eq}"


def test_kill_switch_blocks_trading(cfg):
    frames = _uptrend_frames()
    idx = frames["C0/USDT"].index
    monday = [d for d in idx if d.weekday() == 0][-2]
    clipped = {k: v[v.index <= monday] for k, v in frames.items()}
    state = State(cfg.p("state", "paper.json"))
    state["equity_high"] = 50_000.0  # equity 10k is 80% below this high
    state.save()
    runner, broker, _ = _runner(cfg, clipped, monday)
    out = runner.run_once()
    assert out.get("killed") and broker.holdings()[1] == {}
    assert "KILL SWITCH" in (cfg.workdir / "logs" / "alerts.log").read_text()

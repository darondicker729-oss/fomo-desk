"""Command line: python -m trendbot <command> [--config config.yaml]

  download   fetch and check daily candles for the universe
  research   run every research test on data before holdout_start
  holdout    test the frozen settings once on the holdout period
  run        one paper/live trading pass (schedule daily after 00:00 UTC)
  monitor    health check (schedule hourly)
  carry-download / carry-research   funding carry research (no live trading yet)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from . import config as config_mod


def _exchange(cfg, auth: bool):
    import ccxt

    opts = {"enableRateLimit": True}
    if auth:
        key, secret = os.environ.get("TRENDBOT_API_KEY"), os.environ.get("TRENDBOT_API_SECRET")
        if not key or not secret:
            sys.exit("TRENDBOT_API_KEY and TRENDBOT_API_SECRET must be set for live mode")
        opts.update(apiKey=key, secret=secret)
        if os.environ.get("TRENDBOT_API_PASSWORD"):
            opts["password"] = os.environ["TRENDBOT_API_PASSWORD"]
    ex = getattr(ccxt, cfg.exchange)(opts)
    ex.load_markets()
    return ex


def _broker(cfg, state):
    from .live import LiveBroker, PaperBroker

    if cfg.mode == "live":
        if os.environ.get("TRENDBOT_GO_LIVE") != "yes":
            sys.exit("Config says live, but TRENDBOT_GO_LIVE=yes is not set. Refusing to trade real money.")
        ex = _exchange(cfg, auth=True)
        return ex, LiveBroker(ex, state, cfg)
    ex = _exchange(cfg, auth=False)
    return ex, PaperBroker(state, cfg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trendbot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["download", "research", "holdout", "run", "monitor",
                                        "carry-download", "carry-research"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--random-runs", type=int, default=100)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config_mod.load(a.config)

    if a.command == "carry-download":
        from .carry import download as carry_download
        carry_download(cfg)
    elif a.command == "carry-research":
        from .carry import research as carry_research
        text, ok = carry_research(cfg, n_random=a.random_runs)
        print(text)
        return 0 if ok else 2
    elif a.command == "download":
        from .data import download
        download(cfg)
    elif a.command == "research":
        from .research import research
        text, ok = research(cfg, n_random=a.random_runs)
        print(text)
        return 0 if ok else 2
    elif a.command == "holdout":
        from .research import holdout
        text, ok = holdout(cfg)
        print(text)
        return 0 if ok else 2
    else:
        from .live import Alerts, Runner, State, monitor
        from .research import param_hash
        state = State(cfg.p("state", f"{cfg.mode}.json"))
        alerts = Alerts(cfg)
        try:
            ex, broker = _broker(cfg, state)
            if a.command == "run":
                Runner(cfg, ex, broker, state, alerts, test_id=param_hash(cfg.strategy, cfg)).run_once()
            else:
                return 1 if monitor(cfg, broker, state, alerts) else 0
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 - alert, then fail loudly
            alerts.send(f"MODE: {cfg.mode.upper()} | ERROR in {a.command}: {e!r}. Stops left in place.")
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Load and validate config.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from .strategy import Params


@dataclass
class Config:
    path: Path
    exchange: str
    data_source: str
    quote: str
    universe: list[str]
    benchmark: str
    research_start: pd.Timestamp
    holdout_start: pd.Timestamp
    fee_per_side: float
    slippage_per_side: float
    delist_haircut: float
    strategy: Params
    grid: dict
    gates: dict
    risk: dict
    mode: str
    paper_start_cash: float
    execution: dict
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def workdir(self) -> Path:
        return self.path.parent

    @property
    def cost_per_side(self) -> float:
        return self.fee_per_side + self.slippage_per_side

    def trade_symbol(self, sym: str) -> str:
        """Universe symbol (as in the research data) -> symbol on the trading exchange."""
        base = sym.split("/")[0]
        return f"{base}/{self.quote}"

    def p(self, *parts: str) -> Path:
        out = self.workdir.joinpath(*parts)
        out.parent.mkdir(parents=True, exist_ok=True)
        return out


def load(path: str | Path) -> Config:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text()) or {}
    costs = raw.get("costs", {})
    risk = {"daily_loss_limit": 0.03, "kill_switch_drawdown": 0.20, **raw.get("risk", {})}
    gates = {
        "min_trades": 150, "walk_forward_min_positive": 0.6, "min_deflated_sharpe": 0.90,
        "random_percentile": 90, **raw.get("gates", {}),
    }
    execution = {"max_slippage": 0.003, "stop_limit_gap": 0.01, **raw.get("execution", {})}
    mode = str(raw.get("mode", "paper")).lower()
    if mode not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")
    cfg = Config(
        path=path,
        exchange=raw["exchange"],
        data_source=raw.get("data_source", raw["exchange"]),
        quote=raw.get("quote", "USDT"),
        universe=list(raw["universe"]),
        benchmark=raw.get("benchmark", raw["universe"][0]),
        research_start=pd.Timestamp(raw.get("research_start", "2019-01-01")),
        holdout_start=pd.Timestamp(raw["holdout_start"]),
        fee_per_side=float(costs.get("fee_per_side", 0.001)),
        slippage_per_side=float(costs.get("slippage_per_side", 0.0005)),
        delist_haircut=float(costs.get("delist_haircut", 0.3)),
        strategy=Params.from_dict(raw.get("strategy")),
        grid=raw.get("grid", {}) or {},
        gates=gates,
        risk=risk,
        mode=mode,
        paper_start_cash=float(raw.get("paper_start_cash", 10000)),
        execution=execution,
        raw=raw,
    )
    if cfg.benchmark not in cfg.universe:
        raise ValueError("benchmark must be one of the universe symbols")
    if cfg.holdout_start <= cfg.research_start:
        raise ValueError("holdout_start must be after research_start")
    return cfg

"""Download daily candles, check them, and load them as a panel.

Rules: real data only, UTC dates, closed bars only, gaps stay as gaps.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import requests

from .config import Config

DAY_MS = 86_400_000
COLS = ["timestamp", "open", "high", "low", "close", "volume"]
BINANCE_PUBLIC = "https://data-api.binance.vision/api/v3/klines"


def _now_ms() -> int:
    return int(time.time() * 1000)


def drop_unclosed(rows: list[list], now_ms: int | None = None) -> list[list]:
    now_ms = now_ms or _now_ms()
    return [r for r in rows if r[0] + DAY_MS <= now_ms]


def fetch_binance_public(symbol: str, since_ms: int) -> list[list]:
    """Binance's public market-data mirror. Needs no account or key."""
    pair = symbol.replace("/", "")
    rows: list[list] = []
    since = since_ms
    while True:
        resp = requests.get(BINANCE_PUBLIC, params={
            "symbol": pair, "interval": "1d", "startTime": since, "limit": 1000}, timeout=30)
        if resp.status_code == 400:
            return rows  # unknown symbol
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows += [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                 for k in batch]
        nxt = int(batch[-1][0]) + DAY_MS
        if len(batch) < 1000 or nxt > _now_ms():
            break
        since = nxt
        time.sleep(0.2)
    return rows


def fetch_ccxt(exchange_id: str, symbol: str, since_ms: int) -> list[list]:
    import ccxt  # imported lazily so research works without it

    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    rows: list[list] = []
    since = since_ms
    while True:
        batch = ex.fetch_ohlcv(symbol, "1d", since=since, limit=1000)
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + DAY_MS
        if nxt <= since or nxt > _now_ms():
            break
        since = nxt
    return rows


def download(cfg: Config) -> list[str]:
    since = int(pd.Timestamp(cfg.research_start, tz="UTC").timestamp() * 1000)
    log = []
    for sym in cfg.universe:
        if cfg.data_source == "binance_public":
            rows = fetch_binance_public(sym, since)
        else:
            rows = fetch_ccxt(cfg.data_source, sym, since)
        rows = drop_unclosed(rows)
        df = pd.DataFrame(rows, columns=COLS).drop_duplicates("timestamp").sort_values("timestamp")
        out = cfg.p("data", "raw", cfg.data_source, f"{_fname(sym)}.csv")
        df.to_csv(out, index=False)
        line = (f"| {sym} | {cfg.data_source} | {len(df)} bars | "
                f"{_date(df.timestamp.min()) if len(df) else '-'} → "
                f"{_date(df.timestamp.max()) if len(df) else '-'} | "
                f"downloaded {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC |")
        log.append(line)
        print(line)
    src = cfg.p("data", "sources.md")
    if not src.exists():
        src.write_text("| Symbol | Source | Rows | Range | Downloaded |\n|---|---|---|---|---|\n")
    with src.open("a") as f:
        f.write("\n".join(log) + "\n")
    clean(cfg)
    return log


def check_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Return a daily-indexed frame plus a list of issues. Never invents prices."""
    issues = []
    df = df.copy()
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    dups = df["date"].duplicated().sum()
    if dups:
        issues.append(f"{dups} duplicate dates (kept first)")
        df = df[~df["date"].duplicated()]
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]].astype(float)
    bad = (df[["open", "high", "low", "close"]] <= 0).any(axis=1) | (df["high"] < df["low"]) \
        | (df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))
    if bad.any():
        issues.append(f"{int(bad.sum())} bad bars removed (non-positive or high/low inconsistent): "
                      + ", ".join(str(d.date()) for d in df.index[bad][:5]))
        df = df[~bad]
    if len(df):
        full = pd.date_range(df.index.min(), df.index.max(), freq="D")
        missing = full.difference(df.index)
        if len(missing):
            issues.append(f"{len(missing)} missing days left as gaps: "
                          + ", ".join(str(d.date()) for d in missing[:5]))
        df = df.reindex(full)
        jumps = df["close"].pct_change(fill_method=None).abs()
        big = jumps[jumps > 0.8]
        if len(big):
            issues.append("moves over 80% in a day (kept, check by hand): "
                          + ", ".join(str(d.date()) for d in big.index[:5]))
    return df, issues


def clean(cfg: Config) -> None:
    report = ["# Data check", ""]
    for sym in cfg.universe:
        raw = cfg.p("data", "raw", cfg.data_source, f"{_fname(sym)}.csv")
        if not raw.exists():
            report.append(f"- **{sym}**: no raw file")
            continue
        df, issues = check_frame(pd.read_csv(raw))
        df.to_csv(cfg.p("data", "clean", f"{_fname(sym)}.csv"), index_label="date")
        report.append(f"- **{sym}**: {df['close'].notna().sum()} bars. "
                      + ("; ".join(issues) if issues else "no issues"))
    cfg.p("data", "clean", "report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))


def load_panel(cfg: Config, end: pd.Timestamp | None = None) -> dict[str, pd.DataFrame]:
    frames = {}
    for sym in cfg.universe:
        path = cfg.workdir / "data" / "clean" / f"{_fname(sym)}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing. Run: python -m trendbot download")
        frames[sym] = pd.read_csv(path, index_col="date", parse_dates=True)
    return frames_to_panel(frames, start=cfg.research_start, end=end)


def frames_to_panel(frames: dict[str, pd.DataFrame], start=None, end=None) -> dict[str, pd.DataFrame]:
    idx = None
    for df in frames.values():
        idx = df.index if idx is None else idx.union(df.index)
    idx = pd.date_range(idx.min(), idx.max(), freq="D")
    if start is not None:
        idx = idx[idx >= start]
    if end is not None:
        idx = idx[idx < end]
    return {f: pd.DataFrame({s: df[f].reindex(idx) for s, df in frames.items()}, index=idx)
            for f in ("open", "high", "low", "close")}


def ohlcv_to_frame(rows: list[list]) -> pd.DataFrame:
    df, _ = check_frame(pd.DataFrame(rows, columns=COLS))
    return df


def _fname(sym: str) -> str:
    return sym.replace("/", "-").replace(":", "_")


def _date(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


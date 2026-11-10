"""Daily close history for the long regime (10.11, owner: «настоящая 200-дневная средняя»).

The 15m store on the pipeline holds ~106 days, so the «200-day» average was really a 106-day one. The long regime now also
reads older daily closes:
  1. the 1d candles in the store (scripts/ci_refresh.py keeps ≥ KEEP_DAYS of them for every core coin);
  2. data/daily_closes18.json when present (Binance daily closes of the 18 core coins, not in git);
  3. cnq/agents/outlook_seed.json (Binance daily closes of BTC/ETH/SOL, in git).
Earlier sources win for a day; the 15m candles always win for the days they cover (terminal.report.long_closes)."""
from __future__ import annotations

import json
import os

DAY_MS = 86_400_000
KEEP_DAYS = 400          # what ci_refresh asks for; the regime needs ≥ 200 (+ 90 for the 90-day change)
MIN_DAYS = 250           # fewer stored 1d bars than this → refetch the whole window
SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents", "outlook_seed.json")
LOCAL = os.path.join("data", "daily_closes18.json")


def _seed(path: str = SEED) -> dict[str, dict[int, float]]:
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {s: {int(v["start_day"]) + i: float(c) for i, c in enumerate(v.get("closes") or []) if c}
            for s, v in (d.get("symbols") or {}).items()}


def _local(path: str = LOCAL) -> dict[str, dict[int, float]]:
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and k.endswith("USDT"):
            try:
                out[k[:-4]] = {int(a): float(b) for a, b in v.items() if b}
            except Exception:  # noqa: BLE001
                continue
    return out


def history(store, symbols, now_ms: int, seed_path: str = SEED, local_path: str = LOCAL) -> dict[str, dict[int, float]]:
    """{BingX symbol: {UTC day: close}} of COMPLETED days (today's forming bar is left out)."""
    today = now_ms // DAY_MS
    seed, local = _seed(seed_path), _local(local_path)
    out: dict[str, dict[int, float]] = {}
    for bx in symbols:
        base = bx.replace("-USDT", "")
        m: dict[int, float] = {}
        for src in (seed.get(base) or {}, local.get(base) or {}):
            m.update(src)
        if store is not None:
            try:
                for c in store.candles(bx, "1d", start=now_ms - (KEEP_DAYS + 30) * DAY_MS):
                    m[c.open_time // DAY_MS] = c.close
            except Exception:  # noqa: BLE001
                pass
        out[bx] = {d: c for d, c in m.items() if d < today and d >= today - KEEP_DAYS - 30}
    return out


def refresh(store, rest, symbols, now_ms: int, log=print) -> int:
    """Keep ≥ MIN_DAYS of 1d candles per coin: a short history is refetched over KEEP_DAYS, otherwise only the tail — and
    only when the last stored bar is over 2 days old, so at most about once a day per coin. One BingX request per coin when it does anything."""
    n = 0
    for bx in symbols:
        try:
            first, last, cnt = store.candle_bounds(bx, "1d")
            if last is not None and last >= now_ms - 2 * DAY_MS:
                continue                                  # fresh enough: the 15m candles cover the recent days anyway
            short = last is None or cnt < MIN_DAYS or first > now_ms - MIN_DAYS * DAY_MS
            n += store.upsert_candles(bx, "1d", rest.klines_range(bx, "1d", now_ms - KEEP_DAYS * DAY_MS if short else last, now_ms))
        except Exception as e:  # noqa: BLE001
            log("daily refresh failed", bx, repr(e))
    return n


def sma_map(hist: dict[str, dict[int, float]], rep: dict | None = None) -> dict[str, dict]:
    """{BASE: {"sma200", "days"}} — the true 200-day average of every coin with ≥ 200 daily closes: the regime's own figure for
    the terminal coins (15m days + the older daily closes), the stored daily closes for the others (the lab coins)."""
    out: dict[str, dict] = {}
    for c in (rep or {}).get("coins") or []:
        r = c.get("regime") or {}
        if r.get("sma200") and (r.get("sma_days") or 0) >= 200:
            out[str(c["symbol"]).replace("-USDT", "")] = {"sma200": float(r["sma200"]), "days": 200}
    for bx, m in hist.items():
        base = bx.replace("-USDT", "")
        if base in out or len(m) < 200:
            continue
        cl = [m[d] for d in sorted(m)][-200:]
        out[base] = {"sma200": sum(cl) / 200, "days": 200}
    return out

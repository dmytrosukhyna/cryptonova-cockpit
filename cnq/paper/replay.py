"""Historical Replay — runs discovery → validation → planning → paper trading over
stored candles, strictly in time order. The decision at candle i only sees
candles[:i+1]; the fill happens on candle i+1."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr, resample
from ..risk.engine import ContractSpec, build_plan
from ..setups.discovery import Discovery, DiscoveryParams
from ..setups.validation import critical_failed, validate
from ..store.db import Store
from .simulator import PaperEngine


def higher_tf_trend(h1: list[Candle], upto_time: int, n: int = 20) -> str | None:
    """Simple regime: close vs. SMA(n) on the higher timeframe, known at upto_time."""
    closed = [c for c in h1 if c.close_time <= upto_time]
    if len(closed) < n:
        return None
    closes = np.array([c.close for c in closed[-n:]])
    last, sma = closes[-1], closes.mean()
    if last > sma * 1.002:
        return "up"
    if last < sma * 0.998:
        return "down"
    return "flat"


def run_replay(symbol: str, candles: list[Candle], interval: str, balance: float = 10_000,
               risk_pct: float = 1.0, mode: str = "STRICT_CRYPTO_NOVA", leverage: int = 5,
               spec: ContractSpec | None = None, params: DiscoveryParams | None = None,
               store: Store | None = None, min_score: float = 60.0,
               coverage: float = 1.0, funding: list[dict] | None = None,
               max_open: int = 1, exec_candles: list[Candle] | None = None,
               deriv: dict | None = None) -> dict:
    """exec_candles: optional lower-timeframe candles (e.g. 1m) used for fills, stops and
    targets; decisions still happen on `candles`. Without them, fills are resolved on the
    decision candles with conservative stop-first rules."""
    spec = spec or ContractSpec(symbol)
    exec_map = _exec_map(candles, exec_candles) if exec_candles else None
    disc = Discovery(symbol, candles, params, deriv=deriv)
    engine = PaperEngine(spec)
    a = arrays(candles)
    atr_arr = atr(a["h"], a["l"], a["c"])
    factor = {"1m": 60, "5m": 12, "15m": 4, "1h": 1}.get(interval, 4)
    h1 = resample(candles, factor) if factor > 1 else candles
    funding = sorted(funding or [], key=lambda r: r["time"])
    run_id = uuid.uuid4().hex[:12]
    started = int(time.time() * 1000)
    pending: list = []           # CALLs waiting for next open
    by_family: dict[str, list[float]] = defaultdict(list)
    rejected_calls = 0
    for i in range(len(candles)):
        c = candles[i]
        # 1. fill yesterday's CALLs at this open, then manage positions through this candle
        ex = exec_map[i] if exec_map else None
        first = ex[0] if ex else c
        for s, plan in pending:
            if len(engine.open) < max_open:
                pos = engine.open_position(s.id, plan, first)
                pos.max_bars = s.evidence.get("max_bars")
                s.move("POSITION_OPEN", c.open_time, f"paper fill {plan.entry:.2f}")
            else:
                s.move("CANCELLED", c.open_time, "max open positions")
        pending = []
        steps = ex if ex else [c]
        for e in steps:
            for pos in engine.step(e, float(atr_arr[i])):
                _close_pos(engine, pos, disc, by_family, store, run_id, symbol, c.open_time)
        for pos in engine.open:
            pos.bars_open += 1
        # 2. discovery at the close of candle i
        for s in disc.step(i):
            fr = funding_at(funding, c.open_time)
            fam = by_family.get(s.family, [])
            stats = {"n": len(fam), "ev_r": float(np.mean(fam))} if fam else None
            checks, score = validate(s, float(atr_arr[i]), coverage, higher_tf_trend(h1, c.close_time),
                                     None, fr, stats)
            s.checks, s.score = checks, score
            failed = critical_failed(checks)
            if failed or score < min_score:
                s.move("REJECTED", c.open_time, f"validation: score {score}, critical {failed}")
                rejected_calls += 1
                continue
            plan = build_plan(symbol, s.direction, s.entry, s.stop, balance, risk_pct, spec,
                              leverage=leverage, mode=mode)
            if not plan.ok:
                s.move("REJECTED", c.open_time, "; ".join(plan.warnings))
                rejected_calls += 1
                continue
            s.evidence["plan"] = plan.__dict__
            s.move("READY", c.open_time, f"score {score}")
            if s.entry_type == "LIMIT" and exec_map is not None:
                # the order was resting at the previous close; it fills on THIS bar's exec candles
                if len(engine.open) >= max_open:
                    s.move("CANCELLED", c.open_time, "max open positions"); continue
                filled = False
                for k, e in enumerate(ex or []):
                    pos = engine.open_limit(s.id, plan, e, float(atr_arr[i]))
                    if pos is None:
                        continue
                    filled = True
                    s.move("POSITION_OPEN", c.open_time, f"limit fill {plan.entry:.2f} @1m")
                    if pos.closed:
                        _close_pos(engine, pos, disc, by_family, store, run_id, symbol, c.open_time)
                    else:
                        for e2 in ex[k + 1:]:
                            for p2 in engine.step_one(pos, e2, float(atr_arr[i])):
                                _close_pos(engine, p2, disc, by_family, store, run_id, symbol, c.open_time)
                    break
                if not filled:
                    s.move("EXPIRED", c.open_time, "limit not filled during the bar")
                continue
            if s.entry_type == "LIMIT":
                if len(engine.open) >= max_open:
                    s.move("CANCELLED", c.open_time, "max open positions"); continue
                pos = engine.open_limit(s.id, plan, c, float(atr_arr[i]))
                if pos is None:
                    s.move("EXPIRED", c.open_time, "limit not filled"); continue
                s.move("POSITION_OPEN", c.open_time, f"limit fill {plan.entry:.2f}")
                if pos.closed:
                    rec = engine.trade_record(pos, run_id, symbol)
                    by_family[s.family].append(rec["result_r"])
                    s.move("COMPLETED", c.open_time, f"{rec['exit_kind']} {rec['result_r']:+.2f}R (same candle)")
                    s.evidence["trade"] = rec
                    if store:
                        store.save_paper_trade(rec)
            else:
                pending.append((s, plan))
    if candles:
        engine.force_close(candles[-1])
        for pos in engine.closed:
            if pos.fills[-1].kind == "FORCED" and store:
                store.save_paper_trade(engine.trade_record(pos, run_id, symbol))
    results = [engine.trade_record(p, run_id, symbol) for p in engine.closed]
    summary = summarize(results, disc.setups, rejected_calls)
    if store:
        for s in disc.setups:
            store.save_setup({**s.to_dict(), "symbol": symbol})
            for t, a_, b_, why in s.transitions:
                store.save_transition(s.id, t, a_, b_, why)
        store.save_run(run_id, "replay", started, int(time.time() * 1000),
                       {"symbol": symbol, "interval": interval, "mode": mode, "risk_pct": risk_pct,
                        "min_score": min_score, "candles": len(candles)}, summary)
    return {"run_id": run_id, "summary": summary, "trades": results, "setups": disc.setups}


def _exec_map(candles: list[Candle], exec_candles: list[Candle]) -> list[list[Candle]]:
    """For each decision candle, the exec candles whose open_time falls inside it."""
    out: list[list[Candle]] = [[] for _ in candles]
    times = np.array([c.open_time for c in candles])
    for e in exec_candles:
        j = int(np.searchsorted(times, e.open_time, side="right")) - 1
        if 0 <= j < len(candles) and e.open_time < candles[j].close_time + 1:
            out[j].append(e)
    return out


def _close_pos(engine, pos, disc, by_family, store, run_id, symbol, t) -> None:
    rec = engine.trade_record(pos, run_id, symbol)
    by_family[_family_of(disc, pos.setup_id)].append(rec["result_r"])
    s = next(x for x in disc.setups if x.id == pos.setup_id)
    s.move("COMPLETED", t, f"{rec['exit_kind']} {rec['result_r']:+.2f}R")
    s.evidence["trade"] = rec
    if store:
        store.save_paper_trade(rec)


def _family_of(disc: Discovery, setup_id: str) -> str:
    return next(x.family for x in disc.setups if x.id == setup_id)


def funding_at(funding: list[dict], t: int) -> float | None:
    last = None
    for r in funding:
        if r["time"] <= t:
            last = r["rate"]
        else:
            break
    return last


def summarize(trades: list[dict], setups: list, rejected_calls: int) -> dict:
    rs = [t["result_r"] for t in trades]
    status_counts: dict[str, int] = defaultdict(int)
    for s in setups:
        status_counts[s.status] += 1
    fam: dict[str, dict] = {}
    for t in trades:
        f = next(s.family for s in setups if s.id == t["setup_id"])
        d = fam.setdefault(f, {"n": 0, "sum_r": 0.0, "wins": 0})
        d["n"] += 1; d["sum_r"] += t["result_r"]; d["wins"] += t["result_r"] > 0
    for d in fam.values():
        d["ev_r"] = round(d["sum_r"] / d["n"], 3); d["winrate"] = round(d["wins"] / d["n"], 3)
    eq = np.cumsum(rs) if rs else np.array([0.0])
    dd = float((np.maximum.accumulate(eq) - eq).max()) if rs else 0.0
    return {"trades": len(rs), "net_r": round(float(sum(rs)), 2),
            "ev_r": round(float(np.mean(rs)), 3) if rs else None,
            "winrate": round(float(np.mean([r > 0 for r in rs])), 3) if rs else None,
            "max_drawdown_r": round(dd, 2), "fees_usdt": round(sum(t["fees"] for t in trades), 2),
            "tp_distribution": {k: sum(1 for t in trades if t["tps_hit"] == k) for k in range(6)},
            "setups_by_status": dict(status_counts), "calls_rejected_by_validation": rejected_calls,
            "families": fam}

"""The «Золото» test desk's backtest (04.11.2026) — the SAME rule code the live desk runs (cnq/agents/gold_desk.py).

Data: the release asset lab-data-gold/gold_desk_data.json (scripts/fetch_gold_desk.py, GitHub Actions):
  main  — Binance PAXGUSDT spot 4-hour bars, 2022-01 → today (2022 = indicator warm-up, no trades);
  check — OKX XAUT-USDT spot 4-hour bars, 2022-07 → today (what the live desk trades).
Trading window 2023-01-01 → the last bar; one $10 000 account per rule; costs 0,05 % a side (0,1 % round trip).
Honest choice, written before the run: 6 rules, ONE parameter set each (gold_desk.PARAMS). A rule qualifies with ≥ 10 trades and
a profit factor above 1 in at least 3 of the 4 calendar years 2023–2026; among those the highest MEDIAN yearly PF wins (the total
PF breaks a tie). The neighbours of the winner's parameters are run afterwards as a robustness check only — they never change
the pick. Every variant run is counted in «variants_tried». Benchmark: buy-and-hold on the same bars (0,05 % in, 0,05 % out).

  python scripts/gold_desk_study.py gold_desk_data.json [--md results.md] [--json results.json]
Stdlib only."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.agents import gold_desk as g                                                 # noqa: E402

T0 = int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
YEARS = (2023, 2024, 2025, 2026)
NEIGHBOURS = {"ema_lo": [{"fast": 20, "slow": 100}, {"fast": 50, "slow": 150}, {"fast": 100, "slow": 200}],
              "ema_ls": [{"fast": 20, "slow": 100}, {"fast": 50, "slow": 150}, {"fast": 100, "slow": 200}],
              "breakout": [{"n": 10, "x": 5}, {"n": 40, "x": 20}, {"n": 55, "x": 20}],
              "pullback": [{"ema": 20}, {"ema": 100}, {"r": 2.0}],
              "sma100": [{"n": 50}, {"n": 150}, {"n": 200}],
              "weekend_fade": [{"move": 0.005}, {"move": 0.01}, {"stop": 0.01}]}


def ms(y: int) -> int:
    return int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def to_bars(rows: list) -> list:
    return [{"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in rows]


def run_rule(bars: list, rule: dict, t0: int = T0) -> g.Sim:
    """indicators from the whole history, trades only from t0"""
    s = g.Sim(rule)
    days = g.daily_from_4h(bars)
    s.set_daily([d for d in days if d["t"] + g.DAY <= bars[-1]["t"] + g.H4])
    for i, b in enumerate(bars):
        if b["t"] >= t0:
            s.step(bars, i)
    return s


def pf(nets: list):
    gp, gl = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
    return round(gp / gl, 2) if gl > 0 else (99.0 if gp > 0 else None)


def max_dd(hist: list) -> float:
    peak, dd = None, 0.0
    for e in hist:
        peak = e["equity"] if peak is None else max(peak, e["equity"])
        dd = max(dd, (peak - e["equity"]) / peak * 100)
    return round(dd, 2)


def seg(sim: g.Sim, a: int, b: int) -> dict:
    h = [e for e in sim.history if a <= e["time"] < b]
    if not h:
        return {}
    e0 = next((e["equity"] for e in reversed(sim.history) if e["time"] < a), g.START)
    cl = [c for c in sim.closed if a <= c["closed"] < b]
    nets = [c["net_usd"] for c in cl]
    return {"pct": round((h[-1]["equity"] / e0 - 1) * 100, 2), "trades": len(cl), "pf": pf(nets),
            "win": round(sum(1 for x in nets if x > 0) / len(nets) * 100, 1) if nets else None,
            "dd": max_dd([{"equity": e0}] + h)}


def summary(sim: g.Sim, end: int) -> dict:
    nets = [c["net_usd"] for c in sim.closed]
    days = (end - T0) / g.DAY
    eq = sim.history[-1]["equity"] if sim.history else g.START
    top2 = sum(sorted(nets, reverse=True)[:2])
    tot = sum(nets)
    longs = [c for c in sim.closed if c["side"] == "LONG"]
    return {"trades": len(nets), "win": round(sum(1 for x in nets if x > 0) / len(nets) * 100, 1) if nets else None, "pf": pf(nets),
            "dd": max_dd([{"equity": g.START}] + sim.history), "pct": round((eq / g.START - 1) * 100, 2),
            "cagr": round(((eq / g.START) ** (365 / days) - 1) * 100, 2) if days > 0 and eq > 0 else None,
            "avg_pct": round(statistics.mean(c["pct"] for c in sim.closed), 3) if sim.closed else None,
            "long_share": round(len(longs) / len(nets) * 100) if nets else None,
            "exposure": round(sum(1 for e, f in zip(sim.history, sim.history[1:]) if f["equity"] != e["equity"]) / max(1, len(sim.history)) * 100),
            "top2_share": round(top2 / tot, 2) if tot > 0 else None,
            "years": {str(y): seg(sim, ms(y), ms(y + 1)) for y in YEARS}}


def hold(bars: list) -> dict:
    b = [x for x in bars if x["t"] >= T0]
    q = g.START * (1 - g.FEE) / b[0]["o"]
    h = [{"time": x["t"] + g.H4, "equity": q * x["c"]} for x in b]
    h[-1]["equity"] *= (1 - g.FEE)
    s = g.Sim({"name": "sma100"})
    s.history = h
    end = b[-1]["t"] + g.H4
    days = (end - T0) / g.DAY
    eq = h[-1]["equity"]
    return {"pct": round((eq / g.START - 1) * 100, 2), "cagr": round(((eq / g.START) ** (365 / days) - 1) * 100, 2), "dd": max_dd(h),
            "years": {str(y): seg(s, ms(y), ms(y + 1)) for y in YEARS}}


def qualifies(sm: dict) -> tuple[bool, float | None, list]:
    ys = [sm["years"][str(y)] for y in YEARS]
    pfs = [y.get("pf") for y in ys if y.get("trades")]
    why = []
    if sm["trades"] < 10:
        why.append(f"сделок {sm['trades']} < 10")
    good = sum(1 for p in pfs if p is not None and p > 1)
    if good < 3:
        why.append(f"PF > 1 только в {good} годах из 4")
    med = round(statistics.median(pfs), 2) if pfs else None
    return not why, med, why


def study(bars: list, label: str) -> dict:
    end = bars[-1]["t"] + g.H4
    res = {"label": label, "from": dt.datetime.fromtimestamp(T0 / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
           "to": dt.datetime.fromtimestamp(end / 1000, dt.timezone.utc).strftime("%Y-%m-%d"), "bars": sum(1 for b in bars if b["t"] >= T0),
           "hold": hold(bars), "rules": {}}
    for name in g.RULES:
        sm = summary(run_rule(bars, {"name": name, **g.PARAMS[name]}), end)
        ok, med, why = qualifies(sm)
        res["rules"][name] = {**sm, "qualifies": ok, "median_year_pf": med, "why": why, "params": g.PARAMS[name]}
    return res


def pick(res: dict):
    q = [(k, v) for k, v in res["rules"].items() if v["qualifies"]]
    if not q:
        return None
    return max(q, key=lambda kv: (kv[1]["median_year_pf"] or 0, kv[1]["pf"] or 0))[0]


def fmt(v, suf="", plus=False):
    if v is None:
        return "—"
    s = f"{v:+.1f}" if plus else (f"{v:.2f}" if isinstance(v, float) and abs(v) < 10 and suf == "" else f"{v:.1f}" if isinstance(v, float) else str(v))
    return s.replace(".", ",").replace("-", "−") + suf


def md(main: dict, check: dict, best: str | None, nb: list, tried: int) -> str:
    L = [f"# Стол «Золото» — бэктест ({main['from']} → {main['to']})", "",
         f"Вариантов проверено всего: {tried} (6 правил × 1 набор параметров на PAXG + те же 6 на XAUT + {len(nb)} соседей выбранного).", ""]
    for r in (main, check):
        h = r["hold"]
        L += [f"## {r['label']} — {r['bars']} свечей 4 ч", "",
              f"Просто держать: {fmt(h['pct'], '%', True)} ({fmt(h['cagr'], '% в год', True)}), просадка −{fmt(h['dd'], '%')}; по годам "
              + ", ".join(f"{y}: {fmt(h['years'][y].get('pct'), '%', True)}" for y in h["years"]), "",
              "| Правило | Сделок | Прибыльных | PF | Макс. просадка | Итог | В год | 2023 | 2024 | 2025 | 2026 | PF по годам (медиана) | 2 лучшие сделки / прибыль | Проходит |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for k, v in r["rules"].items():
            ys = v["years"]
            L.append(f"| {k} | {v['trades']} | {fmt(v['win'], '%')} | {fmt(v['pf'])} | −{fmt(v['dd'], '%')} | {fmt(v['pct'], '%', True)} | {fmt(v['cagr'], '%', True)} | "
                     + " | ".join(f"{fmt(ys[str(y)].get('pct'), '%', True)} ({ys[str(y)].get('trades', 0)} · PF {fmt(ys[str(y)].get('pf'))})" for y in YEARS)
                     + f" | {fmt(v['median_year_pf'])} | {fmt(v['top2_share'])} | {'да' if v['qualifies'] else 'нет: ' + '; '.join(v['why'])} |")
        L.append("")
    L += [f"## Выбор: {best or 'ни одно правило не прошло'}", ""]
    if nb:
        L += ["Соседние параметры выбранного правила (только проверка устойчивости, выбор не меняют):", "",
              "| Параметры | Сделок | PF | Просадка | Итог | PF > 1 в годах |", "| --- | --- | --- | --- | --- | --- |"]
        for p, v in nb:
            good = sum(1 for y in YEARS if (v["years"][str(y)].get("pf") or 0) > 1 and v["years"][str(y)].get("trades"))
            L.append(f"| {p} | {v['trades']} | {fmt(v['pf'])} | −{fmt(v['dd'], '%')} | {fmt(v['pct'], '%', True)} | {good} из 4 |")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--md")
    ap.add_argument("--json")
    a = ap.parse_args()
    d = json.load(open(a.data, encoding="utf-8"))
    paxg = to_bars(d["paxg_4h"])
    xaut = to_bars(d["okx"]["XAUT-USDT"]["4H"])
    main_r = study(paxg, "PAXGUSDT спот Binance")
    check_r = study(xaut, "XAUT-USDT спот OKX (сверка; на нём идёт живой стол)")
    best = pick(main_r)
    nb = []
    if best:
        end = paxg[-1]["t"] + g.H4
        for p in NEIGHBOURS[best]:
            nb.append(({**g.PARAMS[best], **p}, summary(run_rule(paxg, {"name": best, **g.PARAMS[best], **p}), end)))
    tried = 2 * len(g.RULES) + len(nb)
    text = md(main_r, check_r, best, nb, tried)
    print(text)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(text)
    if a.json:
        json.dump({"main": main_r, "check": check_r, "best": best, "neighbours": [[p, v] for p, v in nb], "variants_tried": tried},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

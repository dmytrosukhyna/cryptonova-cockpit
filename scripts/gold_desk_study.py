"""The gold test desk's backtest (30.10.2026) — the SAME rule code the live desk runs (cnq/agents/gold_desk.py) on gold history
2023 → today. GitHub Actions only (.github/workflows/research-gold-desk.yml): the sandbox cannot reach exchanges.

Data: PAXGUSDT spot 1-hour candles from the Binance archive (data.binance.vision, the venue history docs/research/gold.md used),
resampled to 4-hour bars; the Binance XAUUSDT / PAXGUSDT perp funding prints (shorts receive them in «trend_fund»); the XAUUSDT
perp 1-hour candles (12.2025 →) as a check of the tradable gold perp. Train = 2023-01-01 → 2024-12-31 (the variant of each family
is chosen there by the Sharpe of daily returns, ≥ 10 trades), test = 2025-01-01 → today (never used for the choice).
A family passes when, at once: test result > 0; train result > 0 in BOTH 2023 and 2024; the worst drawdown of the whole run
< 20 %; the two best trades make < half of the total profit; ≥ 10 closed trades in all (the rules of gold.md, adapted to two
training years). Benchmark: buy-and-hold PAXG spot.
Also: a probe of what OKX serves for gold (instruments XAUT / PAXG / XAU on spot and swap, candle depth) — for the live desk.
Writes $GOLD_DIR/gold_desk_result.json and .md (the workflow uploads them). Stdlib + numpy (for the archive reader)."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                                                    # noqa: E402

import gold_study as gs                                                               # noqa: E402
from cnq.agents import gold_desk as g                                                 # noqa: E402

OUT = os.environ.get("GOLD_DIR", "/tmp/gold")
T_TRAIN0 = int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
T_TEST0 = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def bars4h(a: np.ndarray) -> list:
    out = {}
    for t, o, h, l, c, v in a:
        k = int(t) - int(t) % g.H4
        if k not in out:
            out[k] = {"t": k, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}
        else:
            x = out[k]
            x["h"], x["l"], x["c"], x["v"] = max(x["h"], float(h)), min(x["l"], float(l)), float(c), x["v"] + float(v)
    return [out[k] for k in sorted(out)]


def funding_hours(prints) -> dict:
    """{hour ms: rate per hour} from the perp's funding prints (rate per interval h)"""
    out = {}
    for t, r, h in prints:
        h = int(h) or 8
        for k in range(h):
            out[int(t) - int(t) % 3_600_000 - k * 3_600_000] = float(r) / h
    return out


def daily_returns(hist: list, t0: int, t1: int) -> list:
    by = {}
    for e in hist:
        if t0 <= e["time"] < t1:
            by[e["time"] - e["time"] % g.DAY] = e["equity"]
    ks = sorted(by)
    return [by[b] / by[a] - 1 for a, b in zip(ks, ks[1:])]


def seg(sim: g.Sim, t0: int, t1: int) -> dict:
    """result of one segment [t0, t1): % on the equity at t0, Sharpe of daily returns, max DD, trades closed inside, PF, top-2 share"""
    h = [e for e in sim.history if t0 <= e["time"] < t1]
    if len(h) < 2:
        return {"pct": None}
    e0 = next((e["equity"] for e in reversed(sim.history) if e["time"] < t0), h[0]["equity"])
    r = daily_returns(sim.history, t0, t1)
    sd = float(np.std(r)) if len(r) > 2 else 0.0
    sharpe = float(np.mean(r)) / sd * math.sqrt(365) if sd > 0 else 0.0
    peak, dd = e0, 0.0
    for e in h:
        peak = max(peak, e["equity"])
        dd = max(dd, (peak - e["equity"]) / peak * 100)
    cl = [c for c in sim.closed if t0 <= c["closed"] < t1]
    wins = [c["net_usd"] for c in cl if c["net_usd"] > 0]
    losses = [-c["net_usd"] for c in cl if c["net_usd"] < 0]
    pf = sum(wins) / sum(losses) if losses and sum(losses) > 0 else (99.0 if wins else None)
    tot = sum(c["net_usd"] for c in cl)
    top2 = sum(sorted((c["net_usd"] for c in cl), reverse=True)[:2])
    return {"pct": round((h[-1]["equity"] / e0 - 1) * 100, 2), "sharpe": round(sharpe, 2), "dd_pct": round(dd, 2), "trades": len(cl),
            "winrate": round(len(wins) / len(cl) * 100, 1) if cl else None, "pf": round(pf, 2) if pf is not None else None,
            "top2_share": round(top2 / tot, 2) if tot > 0 else None, "net_usd": round(tot, 2)}


def years(sim: g.Sim, y0: int, y1: int) -> dict:
    out = {}
    for y in range(y0, y1 + 1):
        a = int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        b = int(dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        out[str(y)] = seg(sim, a, b).get("pct")
    return out


def hold(bars: list) -> dict:
    """buy-and-hold PAXG spot: train / test / years, 0,12 % once"""
    h = [{"time": b["t"] + g.H4, "equity": g.START * b["c"] / bars[0]["o"] * (1 - g.FEE_SPOT)} for b in bars]
    s = g.Sim({"name": "trend", "n": 1})
    s.history = h
    now = bars[-1]["t"] + g.H4
    return {"train": seg(s, T_TRAIN0, T_TEST0), "test": seg(s, T_TEST0, now), "years": years(s, 2023, dt.datetime.now(dt.timezone.utc).year)}


def study(bars: list, fund: dict, label: str) -> dict:
    now = bars[-1]["t"] + g.H4
    res = {"label": label, "bars": len(bars), "from": gs.dt.datetime.utcfromtimestamp(bars[0]["t"] / 1000).strftime("%Y-%m-%d"),
           "to": gs.dt.datetime.utcfromtimestamp(now / 1000).strftime("%Y-%m-%d"), "hold": hold(bars), "families": {}}
    ycur = dt.datetime.now(dt.timezone.utc).year
    for fam, variants in g.VARIANTS.items():
        rows = []
        for v in variants:
            sim = g.backtest(bars, {"name": fam, **v}, fund)
            tr, te = seg(sim, T_TRAIN0, T_TEST0), seg(sim, T_TEST0, now)
            whole = seg(sim, bars[0]["t"], now)
            rows.append({"params": v, "train": tr, "test": te, "all": whole, "years": years(sim, 2023, ycur)})
        ok = [r for r in rows if (r["train"].get("trades") or 0) >= 10 and r["train"].get("sharpe") is not None]
        pick = max(ok, key=lambda r: r["train"]["sharpe"]) if ok else None
        passed, why = False, []
        if pick:
            y = pick["years"]
            if not ((pick["test"].get("pct") or 0) > 0):
                why.append("проверка не в плюсе")
            if not all((y.get(k) or 0) > 0 for k in ("2023", "2024")):
                why.append("обучение не в плюсе в оба года")
            if (pick["all"].get("dd_pct") or 0) >= 20:
                why.append(f"просадка {pick['all'].get('dd_pct')}% ≥ 20%")
            if pick["all"].get("top2_share") is None or pick["all"]["top2_share"] >= 0.5:
                why.append("две лучшие сделки ≥ половины прибыли")
            if (pick["all"].get("trades") or 0) < 10:
                why.append("меньше 10 сделок")
            passed = not why
        else:
            why.append("ни один вариант не набрал 10 сделок на обучении")
        test_pos = sum(1 for r in rows if (r["test"].get("pct") or 0) > 0)
        res["families"][fam] = {"variants": rows, "pick": pick, "passed": passed, "why": why,
                                "test_positive": f"{test_pos} из {len(rows)}",
                                "test_median_pct": round(float(np.median([r["test"].get("pct") or 0 for r in rows])), 2)}
    return res


def probe_okx() -> dict:
    out = {}
    for it in ("SPOT", "SWAP"):
        s, d = gs.get(f"https://www.okx.com/api/v5/public/instruments?instType={it}", tries=2)
        rows = (d or {}).get("data") or [] if isinstance(d, dict) else []
        out[it] = [{"inst": x.get("instId"), "state": x.get("state"), "listed": x.get("listTime")} for x in rows
                   if any(k in str(x.get("instId")) for k in ("XAUT", "PAXG", "XAU"))] if s == 200 else f"HTTP {s}"
    for inst in g.INSTS:
        try:
            b4 = g.okx_candles(inst, "4H", 1300, lambda u: gs.get(u, tries=2)[1])
            b1 = g.okx_candles(inst, "1Dutc", 300, lambda u: gs.get(u, tries=2)[1])
            out[inst] = {"4H": len(b4), "4H_from": gs.dt.datetime.utcfromtimestamp(b4[0]["t"] / 1000).strftime("%Y-%m-%d") if b4 else None,
                         "1Dutc": len(b1), "1D_from": gs.dt.datetime.utcfromtimestamp(b1[0]["t"] / 1000).strftime("%Y-%m-%d") if b1 else None,
                         "last_close": b4[-1]["c"] if b4 else None}
        except Exception as e:  # noqa: BLE001
            out[inst] = f"error {str(e)[:120]}"
    return out


def md(res: dict, okx: dict) -> str:
    L = ["# Стол «Золото» — проверка правил на истории (автоматический отчёт)", ""]
    for r in res:
        hd = r["hold"]
        L += [f"## {r['label']}: {r['from']} → {r['to']}, {r['bars']} свечей 4 ч", "",
              f"Просто держать: обучение {hd['train'].get('pct')}%, проверка {hd['test'].get('pct')}% (просадка {hd['test'].get('dd_pct')}%), по годам {hd['years']}", "",
              "| Семья | Выбрано на 2023–24 | Обучение % · Шарп · просадка | Проверка 2025–26 % · Шарп · просадка | Сделок · PF · в плюсе | Две лучшие сделки / прибыль | Итог |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
        for fam, f in r["families"].items():
            p = f["pick"]
            if not p:
                L.append(f"| {fam} | — | — | — | — | — | {'; '.join(f['why'])} |")
                continue
            tr, te, al = p["train"], p["test"], p["all"]
            L.append(f"| {fam} | {p['params']} | {tr.get('pct')} · {tr.get('sharpe')} · −{tr.get('dd_pct')} | {te.get('pct')} · {te.get('sharpe')} · −{te.get('dd_pct')} "
                     f"| {al.get('trades')} · {al.get('pf')} · {al.get('winrate')}% | {al.get('top2_share')} | {'прошло' if f['passed'] else 'не прошло: ' + '; '.join(f['why'])} "
                     f"(в плюсе на проверке {f['test_positive']}, медиана {f['test_median_pct']}%) |")
        L.append("")
        L.append("По годам (выбранные варианты): " + json.dumps({k: v["pick"]["years"] for k, v in r["families"].items() if v["pick"]}, ensure_ascii=False))
        L.append("")
    L += ["## OKX", "", "```", json.dumps(okx, ensure_ascii=False, indent=1), "```"]
    return "\n".join(L)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    paxg, months = gs.vision_series("spot", "PAXGUSDT")
    paxg = paxg[paxg[:, 0] >= T_TRAIN0 - 250 * g.DAY]                      # warm-up for SMA 200 before 2023
    fund = {}
    for sym in ("XAUUSDT", "PAXGUSDT"):
        pr, _m = gs.vision_funding(sym)
        if pr:
            fund = {**funding_hours(pr), **fund} if sym == "PAXGUSDT" else funding_hours(pr)    # XAU perp first, PAXG where XAU has none
    bars = bars4h(paxg)
    res = [study(bars, fund, "PAXGUSDT спот Binance (4 ч из часовых)")]
    xau, _m = gs.vision_series("um", "XAUUSDT")
    if xau is not None and len(xau) > 24 * 60:
        xb = bars4h(xau)
        sims = {}
        for fam, f in res[0]["families"].items():
            if f["pick"]:
                s = g.backtest(xb, {"name": fam, **f["pick"]["params"]}, fund)
                sims[fam] = {"params": f["pick"]["params"], "pct": seg(s, xb[0]["t"], xb[-1]["t"] + g.H4)}
        res.append({"label": "XAUUSDT бессрочный Binance — те же выбранные варианты (без обучения; короткая история)",
                    "bars": len(xb), "from": gs.dt.datetime.utcfromtimestamp(xb[0]["t"] / 1000).strftime("%Y-%m-%d"),
                    "to": gs.dt.datetime.utcfromtimestamp(xb[-1]["t"] / 1000).strftime("%Y-%m-%d"), "hold": hold(xb),
                    "families": {k: {"variants": [], "pick": {"params": v["params"], "train": {}, "test": v["pct"], "all": v["pct"], "years": {}},
                                     "passed": False, "why": ["только сверка"], "test_positive": "—", "test_median_pct": None} for k, v in sims.items()}})
    okx = probe_okx()
    with open(f"{OUT}/gold_desk_result.json", "w", encoding="utf-8") as f:
        json.dump({"results": res, "okx": okx, "funding_hours": len(fund), "archive_months": months[-1:]}, f, ensure_ascii=False, indent=1)
    text = md(res, okx)
    with open(f"{OUT}/gold_desk_result.md", "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    for fam, f in res[0]["families"].items():
        p = f["pick"] or {}
        gs.note(f"gold desk · {fam}", f"{p.get('params')} train {p.get('train', {}).get('pct')}% test {p.get('test', {}).get('pct')}% "
                                     f"dd {p.get('all', {}).get('dd_pct')}% trades {p.get('all', {}).get('trades')} → {'PASS' if f['passed'] else 'fail: ' + '; '.join(f['why'])}")
    gs.note("gold desk · okx", json.dumps(okx, ensure_ascii=False)[:3500])


if __name__ == "__main__":
    main()

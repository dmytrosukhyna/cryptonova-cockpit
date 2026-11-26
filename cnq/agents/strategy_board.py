"""Daily strategy review (owner 22.11.2026 16:18): one table of every desk over 7 and 30 days — trades, win rate, PF, average R, max
drawdown — ranked, with a flag on any desk that beats the real account's source with enough trades (≥ 30 trades, PF > 1.2, positive
in BOTH windows, and a better PF than the real source). Owner-only data: the server builds it from the desks' own files
(<run>/data/...) and the public feeds; numbers only, no trader ids. Written to <run>/data/strategy_board.json, served at
/v2/owner/strategy_board, and once a day (after 18:30 UTC, before the 21:07 Athens evening report) published as
docs/research/strategy_board.md. Paper desks are never routed to the real account. Stdlib only."""
from __future__ import annotations

import glob
import json
import os
import time

DAY = 86_400_000
WINDOWS = (7, 30)
START = 10_000.0


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _t(c: dict):
    for k in ("closed_t", "close_t", "closed", "close_time", "settled_t", "exit_t", "end_t"):
        v = _f(c.get(k))
        if v:
            return int(v if v > 1e12 else v * 1000)
    return None


def norm(items, start: float | None = None, pct_only: bool = False) -> list:
    """closed trades of any desk → [{t, usd, pct, r}] (usd and pct may be None)"""
    out = []
    for c in items or []:
        if not isinstance(c, dict):
            continue
        t = _t(c)
        if not t:
            continue
        usd = None if pct_only else next((v for v in (_f(c.get(k)) for k in ("pnl_usd", "net_usd", "usd", "pnl", "closedPnl", "profit")) if v is not None), None)
        pct = next((v for v in (_f(c.get(k)) for k in ("pct", "move_pct", "ret", "pnl_pct", "score_pct")) if v is not None), None)
        r = next((v for v in (_f(c.get(k)) for k in ("pnl_r", "r", "R")) if v is not None), None)
        out.append({"t": t, "usd": usd, "pct": pct, "r": r})
    return sorted(out, key=lambda x: x["t"])


def stats(trades: list, start: float, since: int) -> dict:
    xs = [x for x in trades if x["t"] >= since]
    if not xs:
        return {"n": 0}
    key = "usd" if all(x["usd"] is not None for x in xs) else "pct"
    v = [x[key] or 0.0 for x in xs]
    pos, neg = sum(a for a in v if a > 0), -sum(a for a in v if a < 0)
    cum = peak = dd = 0.0
    for a in v:
        cum += a
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    rs = [x["r"] for x in xs if x["r"] is not None]
    base = start if key == "usd" else 100.0
    return {"n": len(xs), "win": round(sum(a > 0 for a in v) / len(v) * 100, 1), "pf": round(pos / neg, 2) if neg else (None if not pos else 99.0),
            "sum": round(sum(v), 2), "unit": "$" if key == "usd" else "%", "ret_pct": round(sum(v) / base * 100, 2) if key == "usd" else round(sum(v), 2),
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None, "max_dd_pct": round(dd / base * 100, 2) if key == "usd" else round(dd, 2)}


def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def collect(web: str, data: str) -> dict:
    """{desk key: (name, trades, start $)} from the files the desks already keep — a missing file = the desk is left out"""
    D = {}
    pr = _load(os.path.join(web, "public_real.json")) or {}
    arch = _load(os.path.expanduser("~/.cryptonova_public_real_archive_2210.json")) or {}
    fut_base = (_f(pr.get("start_usd")) or START) * 0.15
    D["real"] = ("Реальный счёт · фьючерсы", norm(list(pr.get("closed") or []) + list(arch.get("closed") or [])), fut_base)
    wl = (_load(os.path.join(web, "whales_live.json")) or {}).get("desk") or {}
    D["whales"] = ("Киты (бумажный стол)", norm(wl.get("closed")), _f(wl.get("start")) or START)
    pp = _load(os.path.join(web, "paper.json")) or {}
    D["own"] = ("Свои сигналы", norm(pp.get("closed")), START)
    for k, nm in (("cons", "Автопилот · консервативный"), ("bal", "Автопилот · сбалансированный"), ("aggr", "Автопилот · агрессивный")):
        D[f"ap_{k}"] = (nm, norm(((pp.get("profiles") or {}).get(k) or {}).get("closed")), START)
    gd = _load(os.path.join(web, "gold_desk.json")) or {}
    if gd:
        D["gold"] = ("Золото (тест)", norm(gd.get("closed")), _f(gd.get("start_equity")) or START)
    for aid_p in sorted(glob.glob(os.path.join(data, "ivan_tests", "state_*.json"))):
        st = _load(aid_p) or {}
        aid = os.path.basename(aid_p)[6:-5]
        D[f"ivan_{aid}"] = (f"Иван тест {aid}", norm(st.get("closed")), START)
    for key, nm, fn in (("reversal", "Разворот", "reversal/state.json"), ("smart_alts", "Альты от китов", "smart_alts/state.json"),
                        ("levels", "Уровни", "levels/state.json"), ("qm", "Квазимодо 4ч · лонг", "qm/state.json"), ("hacks", "Взломы → шорт", "hacks/state.json"),
                        ("manual", "Ручной стол", "manual_desk/desk.json"), ("listing_short", "Шорт листингов", "listing_short/state.json")):
        st = _load(os.path.join(data, fn))
        if st:
            D[key] = (nm, norm(st.get("closed")), _f(st.get("start")) or START)
    mc = _load(os.path.join(data, "my_calls", "calls.json"))
    if mc:
        calls = mc if isinstance(mc, list) else mc.get("calls") or []
        D["my_calls"] = ("Мои прогнозы", norm([c for c in calls if isinstance(c, dict) and c.get("status") == "closed"], pct_only=True), START)
    pl = _load(os.path.join(data, "peter", "peter_live.json")) or {}
    if pl.get("closed"):
        D["golden_donut"] = ("Golden Donut", norm(pl.get("closed")), START)
    ex = _load(os.path.join(data, "exp_desks", "state.json")) or {}
    if ex.get("closed"):
        from . import exp_desks as X
        for v in X.VARIANTS:
            D[f"exp_{v}"] = (f"Эксперимент · {X.NAMES_RU[v]}", norm([{**c, "usd": None} for c in ex["closed"] if c["v"] == v], pct_only=True), START)
    return D


def board(D: dict, now: int) -> dict:
    rows = []
    for k, (name, tr, start) in D.items():
        row = {"key": k, "name": name, **{f"d{w}": stats(tr, start, now - w * DAY) for w in WINDOWS}}
        rows.append(row)
    real = next((r for r in rows if r["key"] == "whales"), None) or {}       # the real account's SOURCE = the whale copy stream
    rpf = (real.get("d30") or {}).get("pf") or 0.0
    for r in rows:
        a, b = r["d7"], r["d30"]
        r["beats_source"] = bool(r["key"] not in ("whales", "real") and b.get("n", 0) >= 30 and (b.get("pf") or 0) > 1.2
                                 and (a.get("sum") or 0) > 0 and (b.get("sum") or 0) > 0 and (b.get("pf") or 0) > rpf)
    rows.sort(key=lambda r: (-(r["d30"].get("n", 0) >= 10), -((r["d30"].get("pf") or 0) if r["d30"].get("n") else -1), -(r["d30"].get("ret_pct") or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"asof": now, "rows": rows, "source_pf_30d": rpf,
            "rule_ru": "Флаг «лучше источника»: ≥ 30 сделок за 30 дней, PF > 1,2, плюс и за 7, и за 30 дней, PF выше потока китов. "
                       "Перевод на реальный счёт — только после ≥ 30 сделок вперёд, PF и просадки лучше текущего источника и «да» владельца."}


def _c(s: dict, k: str, fmt: str = "{:g}") -> str:
    v = s.get(k)
    return "—" if v is None else fmt.format(v)


def markdown(b: dict) -> str:
    d = time.strftime("%d.%m.%Y %H:%M UTC", time.gmtime(b["asof"] / 1000))
    L = [f"# Доска стратегий — {d}", "", "Все столы за 7 и 30 дней (закрытые сделки). PF — прибыль / убыток; просадка — от старта стола "
         "(для столов в % — в процентных пунктах). Бумажные столы никогда не идут на реальный счёт.", "",
         "| # | Стол | 7д: сделок | 7д: win % | 7д: PF | 7д: итог | 30д: сделок | 30д: win % | 30д: PF | 30д: итог | ср. R | макс. просадка 30д | лучше источника |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in b["rows"]:
        a, c = r["d7"], r["d30"]
        it = lambda s: "—" if not s.get("n") else (f"{s['ret_pct']:+.2f}%")       # noqa: E731
        L.append(f"| {r['rank']} | {r['name']} | {a.get('n', 0)} | {_c(a, 'win')} | {_c(a, 'pf')} | {it(a)} | {c.get('n', 0)} | {_c(c, 'win')} | "
                 f"{_c(c, 'pf')} | {it(c)} | {_c(c, 'avg_r')} | {_c(c, 'max_dd_pct', '{:.2f}%')} | {'**да**' if r['beats_source'] else '—'} |")
    flagged = [r["name"] for r in b["rows"] if r["beats_source"]]
    L += ["", b["rule_ru"], "", ("Лучше источника: " + ", ".join(flagged) + " — предложить владельцу с цифрами.") if flagged
          else "Ни один стол пока не прошёл планку «лучше источника»."]
    g = b.get("spot_gain50")
    if g is not None:
        L += ["", "Спот с прибылью ≥ +50% (фиксация прибыли выключена владельцем 23.11 — вынести ему на решение): "
              + (", ".join(f"{x['sym']} {x['pnl_pct']:+.0f}%" for x in g) or "нет")]
    f = b.get("factory")
    if f:
        L += ["", f"## Фабрика экспериментов — живых спецификаций {f['live']} из {f['specs']}, закрытых сделок {f['closed']}, "
              f"цикл {f.get('cycle_ms')} мс", "",
              "| # | Спецификация | Семейство | Сделок | Win % | PF | Среднее % | t | Просадка | Кандидат |", "|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(f["top"], 1):
            L.append(f"| {i} | `{r['id']}` | {r.get('family')} | {r.get('n', 0)} | {_c(r, 'win')} | {_c(r, 'pf')} | {_c(r, 'mean')} | {_c(r, 't')} | "
                     f"{_c(r, 'max_dd')} | {'**да**' if r.get('candidate') else '—'} |")
        L += ["", "Закрыто за сутки (мертвые): " + (", ".join(f"`{k}`" for k in f["killed"]) or "нет"),
              "Новые за сутки (мутации лидеров): " + (", ".join(f"`{k}`" for k in f["spawned"]) or "нет")]
    return "\n".join(L) + "\n"


def factory_part(data: str, now: int, src: dict | None) -> dict | None:
    """22.11 16:48: the experiment factory's top 10 and today's killed / spawned specs"""
    st = _load(os.path.join(data, "exp_factory", "state.json"))
    if not st:
        return None
    from . import exp_factory as F
    sm = F.summary(st, {"pf": (src or {}).get("pf"), "max_dd": (src or {}).get("max_dd_pct")})
    day = [g for g in st.get("generations") or [] if now - int(g.get("t") or 0) <= DAY]
    return {"live": sm["live"], "specs": sm["specs"], "cycle_ms": sm["cycle_ms"], "closed": sm["closed"],
            "top": [r for r in sm["rows"] if r.get("n", 0) >= 1][:10], "candidates": [r["id"] for r in sm["rows"] if r["candidate"]],
            "killed": [k for g in day for k in g.get("killed") or []], "spawned": [k for g in day for k in g.get("spawned") or []]}


def write(web: str, data: str, now: int | None = None) -> dict:
    now = int(now or time.time() * 1000)
    b = board(collect(web, data), now)
    src = next((r["d30"] for r in b["rows"] if r["key"] == "whales"), {})
    b["factory"] = factory_part(data, now, src)
    try:                                   # 23.11 (owner): profit-taking is off — list the spot coins at ≥ +50 % for his decision
        pf = _load(os.path.join(web, "portfolio.json")) or {}
        b["spot_gain50"] = [{"sym": p["symbol"], "pnl_pct": p.get("pnl_pct")} for p in ((pf.get("portfolios") or {}).get("ideas") or {}).get("positions") or []
                            if isinstance(p.get("pnl_pct"), (int, float)) and p["pnl_pct"] >= 50]
    except Exception:  # noqa: BLE001
        b["spot_gain50"] = []
    os.makedirs(data, exist_ok=True)
    with open(os.path.join(data, "strategy_board.json"), "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False)
    return b

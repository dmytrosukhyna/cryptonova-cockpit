"""Standing trader scout (owner 27.11.2026 09:39: «поиск трейдеров — постоянно, пока не будет 5–7 стабильных»). PAPER for column B;
the REAL follow list stays with cnq/agents/whale_study.py (its strict bar + the «хедж» gate) — the scout never adds anyone to it.

Runs in GitHub Actions (.github/workflows/trader-scout.yml, daily; weekly once column A holds ≥ 5 stable traders) on the whale
study's database (ws_trips, Actions cache), the Hyperliquid leaderboard and the forward paper results the server publishes
(docs/research/scout_forward.json). Each run:
  A (strict, eligible for real) = web/whales_top.json traders with «eligible» (the study's 60-day bar incl. open positions) and no
      «хедж» flag — counted and listed; «stable» = also PF ≥ 2 and a trade in the last 7 days.
  B (relaxed, paper) = scripts/research/hedge_pool.py relaxed bar on his own decisions (30 days, win ≥ 60 %, PF ≥ 1.5, ≥ 15 decisions,
      a trade in the last 7 days, both 15-day halves positive) and no «хедж» flag (flags recomputed for every candidate).
  new B → paper at once: config/exp_watch_traders.json («ws» for Binance / OKX ≤ 10, «hl» for Hyperliquid ≤ 30); the factory makes a
      solo spec per trader and the «≥ 2 agree» group specs itself (exp_factory.ensure_watch_specs).
  promotion B → A-candidate: ≥ 30 forward paper copies, PF ≥ 1.5, max drawdown of the copies ≤ ½ of their gain, still no flag — a
      LABEL in the log and the state; the real list changes only through whale_study.
  demotion from B: no trade for 14 days, a new «хедж» flag, or decay (PF of his last 15 decisions < 1, or the forward copies' PF < 1
      after ≥ 20) → removed from the paper sources.
  Bybit / Bitget / BingX copy-trading pages re-probed weekly; a venue that answers with a list is reported.
State: config/scout_state.json; log: docs/research/scout_log.md (newest day first)."""
from __future__ import annotations

import json
import os
import statistics as st
import sys
import time

DAY = 86_400_000
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WATCH = os.path.join(ROOT, "config", "exp_watch_traders.json")
STATE = os.path.join(ROOT, "config", "scout_state.json")
FLAGS = os.path.join(ROOT, "config", "hedge_flags.json")
LOG = os.path.join(ROOT, "docs", "research", "scout_log.md")
FWD = os.path.join(ROOT, "docs", "research", "scout_forward.json")
MAX_WS, MAX_HL = 10, 30
STABLE_TARGET = 5


def _load(p, d=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return d


def column_a(wt: dict, flags: dict, now: int, active: dict | None = None) -> list:
    """the strict bar (whale_study «eligible») minus «хедж»; stable = also passing the bar 3+ days in a row (follow_lock «passing»),
    PF ≥ 2 and a trade in the last 7 days (ws_trips)"""
    passing = (wt.get("follow_lock") or {}).get("passing") or {}
    out = []
    for x in (wt.get("top") or []) + (wt.get("others") or []):
        if not isinstance(x, dict) or not x.get("eligible") or str(x.get("addr", "")).lower() in flags:
            continue
        a = x["addr"]
        p0 = (passing.get(a) or [None])[0]
        last = (active or {}).get(a)
        stable = bool((x.get("pf") or 0) >= 2 and p0 and now - int(p0) >= 3 * DAY and last and now - last <= 7 * DAY)
        out.append({"addr": a, "name": x.get("name"), "win": x.get("win"), "pf": x.get("pf"), "decisions": x.get("decisions"),
                    "pnl_60d": x.get("pnl_60d"), "stable": stable})
    return out


def promote_demote(state: dict, fwd: dict, flags: dict, active: dict, recent_pf: dict, now: int) -> tuple[list, list]:
    """state["B"] = {addr: {...}}; fwd = {addr: {n, pf, max_dd, gain}}; active = {addr: last trade ms}; → (promoted, demoted)"""
    prom, dem = [], []
    for a, b in list((state.get("B") or {}).items()):
        f = fwd.get(a) or {}
        why = None
        if a.lower() in flags:
            why = "новый флаг «хедж»: " + ", ".join(flags[a.lower()])
        elif active.get(a) is not None and now - active[a] > 14 * DAY:
            why = f"нет сделок {round((now - active[a]) / DAY)} дн."
        elif recent_pf.get(a) is not None and recent_pf[a] < 1:
            why = f"спад: PF последних 15 решений {recent_pf[a]:.2f}"
        elif f.get("n", 0) >= 20 and (f.get("pf") or 0) < 1:
            why = f"спад: PF копий вперёд {f.get('pf')} после {f.get('n')}"
        if why:
            dem.append({"addr": a, "why_ru": why, **{k: f.get(k) for k in ("n", "pf")}})
            state["B"].pop(a)
            continue
        if not b.get("promoted") and f.get("n", 0) >= 30 and (f.get("pf") or 0) >= 1.5 and (f.get("max_dd") or 0) <= 0.5 * max(0.0, f.get("gain") or 0):
            b["promoted"] = now
            prom.append({"addr": a, **{k: f.get(k) for k in ("n", "pf", "max_dd", "gain")}})
    return prom, dem


def apply_watch(watch: dict, state: dict) -> dict:
    """the B column → the factory's paper sources (keeps the earlier entries that are still in B, ≤ the caps)"""
    B = state.get("B") or {}
    ws = [x for x in watch.get("ws") or [] if x.get("addr") in B] + [{"addr": a, "why_ru": b.get("why_ru", "")} for a, b in B.items()
                                                                    if b["src"] != "hl" and a not in {x.get("addr") for x in watch.get("ws") or []}]
    hl = [x for x in watch.get("hl") or [] if x.get("addr") in B or x.get("keep")] + [{"addr": a, "why_ru": b.get("why_ru", "")} for a, b in B.items()
                                                                                    if b["src"] == "hl" and a not in {x.get("addr") for x in watch.get("hl") or []}]
    return {**watch, "ws": ws[:MAX_WS], "hl": hl[:MAX_HL]}


def log_md(day: str, s: dict, old: str = "") -> str:
    L = [f"## {day}",
         f"- Проверено кандидатов: Binance / OKX {s['screened_ws']}, Hyperliquid {s['screened_hl']}.",
         f"- Колонка A (строгий отбор, можно на реальный): {s['a_n']} (стабильных {s['a_stable']}): " + (", ".join(s["a_names"][:10]) or "—"),
         f"- Колонка B (мягкий отбор, бумага): {s['b_n']}; добавлено сегодня {len(s['added'])}" + (": " + ", ".join(s["added"][:10]) if s["added"] else ""),
         f"- Повышены B → A-кандидат (≥ 30 копий вперёд, PF ≥ 1,5): " + (", ".join(f"{p['addr']} (n {p['n']}, PF {p['pf']})" for p in s["promoted"]) or "нет"),
         f"- Понижены / убраны из B: " + ("; ".join(f"{d['addr']} — {d['why_ru']}" for d in s["demoted"]) or "нет")]
    if s.get("venues"):
        L.append("- Площадки (раз в неделю): " + ", ".join(f"{k}: {v}" for k, v in s["venues"].items()))
    L.append(f"- Режим поиска: {'раз в неделю (в A ≥ 5 стабильных)' if s['weekly'] else 'ежедневно (пока в A меньше 5 стабильных)'}.")
    head = "# Журнал поиска трейдеров (cnq/agents/trader_scout.py)\n\nКолонка B — только бумага. Реальный список следования меняет только whale_study (строгий отбор + фильтр «хедж»). Новые дни сверху.\n\n"
    body = old.split("\n", 4)[-1] if old.startswith("# Журнал") else old
    return head + "\n".join(L) + "\n\n" + body.lstrip()


def main(db: str):
    sys.path.insert(0, os.path.join(ROOT, "scripts", "research"))
    import hedge_pool as HP
    import sqlite3
    from collections import defaultdict
    now = int(time.time() * 1000)
    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    state = _load(STATE, {}) or {}
    state.setdefault("B", {})
    if not state.get("seeded"):                       # the first run: the paper sources picked by hand on 26.11 are column B
        w0 = _load(WATCH, {}) or {}
        for x in w0.get("ws") or []:
            state["B"].setdefault(x["addr"], {"src": x["addr"].split(":")[0], "added": now, "why_ru": x.get("why_ru", "")})
        state["seeded"] = now
    a_prev = state.get("a_stable", 0)
    if a_prev >= STABLE_TARGET and now - int(state.get("last_run") or 0) < 7 * DAY - 3_600_000 and not os.environ.get("SCOUT_FORCE"):
        print("::notice title=scout::weekly mode — not due")
        return
    hfl = _load(FLAGS, {}) or {}
    flags = {str(k).lower(): v for k, v in (hfl.get("flags") or {}).items()}
    wt = _load(os.path.join(ROOT, "web", "whales_top.json"), {}) or {}
    c = sqlite3.connect(db)
    rows = c.execute("SELECT src, trader, coin, side, open_t, close_t, entry, exit, notional, pnl FROM ws_trips "
                     "WHERE coin IS NOT NULL AND coin != '' ORDER BY open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        by[tid].append(r)
    t_end = max((r[5] for r in rows if r[5]), default=now)
    t30, t60 = t_end - 30 * DAY, t_end - 60 * DAY
    active, recent_pf, cand = {}, {}, {}
    for tid, lst in by.items():
        if tid.split(":")[0] not in ("bn", "okx"):
            continue
        active[tid] = max(max(r[4], r[5] or 0) for r in lst)
        ds = [e for e in HP.decisions(lst) if e["close_t"] > 0]
        last15 = [e["pnl"] for e in ds[-15:]]
        if len(last15) >= 10:
            p, n_ = sum(x for x in last15 if x > 0), -sum(x for x in last15 if x < 0)
            recent_pf[tid] = (p / n_) if n_ else 99.0
        b = HP.own_bar(ds, t30, t_end - 15 * DAY)
        if b["n"] >= 15 and b["win"] >= 0.6 and b["pf"] >= 1.5 and active[tid] >= now - 7 * DAY and b["h1"] > 0 and b["h2"] > 0:
            cand[tid] = b
    # «хедж» flags for the B candidates and the current B (recomputed every run)
    for tid in set(cand) | {a for a, b in state["B"].items() if b["src"] != "hl"}:
        if tid in by:
            trips = defaultdict(list)
            for r in by[tid]:
                trips[(r[2].upper(), r[3])].append((r[4], r[5] or None))
            fl = HP.flags_ws(tid, HP.decisions(by[tid]), trips, t60).get("flags") or []
            if fl:
                flags[tid.lower()] = fl
    # Hyperliquid: leaderboard candidates (as hedge_pool) + the current B wallets
    lb = HP.http(HP.LB, None, 120) or {}
    hrows = []
    for x in lb.get("leaderboardRows") or []:
        m = {k: v for k, v in (x.get("windowPerformances") or [])}
        av, pm = HP.f(x.get("accountValue")), HP.f((m.get("month") or {}).get("pnl"))
        if av >= 1e5 and pm > 0:
            hrows.append(((x.get("ethAddress") or "").lower(), av, pm))
    hc = [a for a, _, _ in sorted(hrows, key=lambda r: -r[2] / r[1])[:80]] + [a for a, b in state["B"].items() if b["src"] == "hl"]
    hc = list(dict.fromkeys(hc))
    hl_ok = {}
    for a in hc:
        try:
            w = HP.hl_wallet(a, now)
        except Exception:  # noqa: BLE001
            continue
        active[a] = now - int((w.get("last_d") or 999) * DAY)
        if w.get("flags"):
            flags[a] = w["flags"]
        if w.get("n", 0) >= 15 and w.get("win", 0) >= 0.6 and w.get("pf", 0) >= 1.5 and (w.get("last_d") or 99) <= 7 and w["h1"] > 0 and w["h2"] > 0 \
                and not w.get("flags"):
            hl_ok[a] = w
    A = column_a(wt, flags, now, active)
    a_set = {x["addr"] for x in A}
    added = []
    for tid, b in sorted(cand.items(), key=lambda kv: -kv[1]["pf"]):
        if tid.lower() in flags or tid in a_set or tid in state["B"]:
            continue
        state["B"][tid] = {"src": tid.split(":")[0], "added": now, "why_ru": f"{day} разведчик: 30 дн {b['n']} решений, {b['win'] * 100:.0f}% прибыльных, PF {b['pf']}"}
        added.append(tid)
    for a, w in sorted(hl_ok.items(), key=lambda kv: -kv[1]["pf"]):
        if a in state["B"]:
            continue
        state["B"][a] = {"src": "hl", "added": now, "why_ru": f"{day} разведчик: 30 дн {w['n']} решений, {w['win'] * 100:.0f}% прибыльных, PF {w['pf']}"}
        added.append(a)
    fwd = (_load(FWD, {}) or {}).get("traders") or {}
    prom, dem = promote_demote(state, fwd, flags, active, recent_pf, now)
    watch = _load(WATCH, {}) or {}
    for x in watch.get("hl") or []:                       # the wallets put there by hand before the scout keep their place
        if x.get("addr") not in state["B"] and not str(x.get("why_ru", "")).startswith(day) and "разведчик" not in str(x.get("why_ru", "")):
            x["keep"] = True
    watch = apply_watch(watch, state)
    venues = {}
    if now - int(state.get("venues_t") or 0) >= 7 * DAY:
        import global_traders as GT
        for k in ("bybit", "bitget", "bingx"):
            s_, b_ = GT.http(GT.PROBES[k], None, 20)
            venues[k] = f"HTTP {s_}" + (" — есть список" if s_ == 200 and b_ and b"list" in b_[:4000].lower() else "")
        state["venues_t"] = now
    stable = sum(1 for x in A if x["stable"])
    state.update(last_run=now, a_n=len(A), a_stable=stable, a=[x["addr"] for x in A])
    s = {"screened_ws": len(by), "screened_hl": len(hc), "a_n": len(A), "a_stable": stable, "a_names": [str(x.get("name") or x["addr"]) for x in A],
         "b_n": len(state["B"]), "added": added, "promoted": prom, "demoted": dem, "venues": venues, "weekly": stable >= STABLE_TARGET}
    hfl["flags"] = {k: v for k, v in sorted(flags.items())}
    hfl["flagged"] = sorted(hfl["flags"])
    json.dump(hfl, open(FLAGS, "w"), ensure_ascii=False, indent=1)
    json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
    json.dump(watch, open(WATCH, "w"), ensure_ascii=False, indent=1)
    old = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
    open(LOG, "w", encoding="utf-8").write(log_md(day, s, old))
    print("::notice title=scout::" + json.dumps({k: s[k] for k in ("screened_ws", "screened_hl", "a_n", "a_stable", "b_n", "added", "promoted", "demoted",
                                                                  "venues", "weekly")}, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main(sys.argv[1])

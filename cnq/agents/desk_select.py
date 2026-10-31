"""«Автовыбор основного стола» (28.10.2026, owner, real money: «система должна автоматически определять самую эффективную систему
и переключаться на нее, когда нужно»). Chooses the MAIN futures desk of FUTURES · CRYPTO NOVA — the desk the real OKX futures
follow (cnq/execution/okx_futures.py) and the panel header counts (cnq/agents/panel_header.py nova_base). «Импульс BTC» (imp)
always stays the second half; «Сигналы CRYPTO NOVA» still join after their own gate. The owner's hand choice of 28.10 («Агрессивная»,
commit 036f1bb09) is the starting point.

Why slow (research 19.10): «switch to whatever worked the last days» and «more money after a winning stretch» LOSE money, and
cons / bal / aggr are the SAME signals at different sizes — ranking them by recent profit only scales up after wins. So the rule
ranks by result ÷ max drawdown (scale-free) and switches only when ALL of these hold:
  • the challenger is a candidate: a futures desk of web/paper.json whose history check is not failed (conveyor.DESKS ev partial /
    passed / oos; not lab, brk, imp, spot or paper-only; not retired by the conveyor);
  • it has ≥ 20 closed demo trades since 17.10;
  • its max drawdown since 17.10 is within what its history promised (conveyor.DESKS dd; the deeper of the window's curve and
    the desk's own stats.max_dd_pct);
  • over the same window (17.10, or the later start of the two) its result ÷ drawdown is positive and beats the current desk's by
    ≥ 20 % (the drawdown never counts below 1 %, so a desk with no dip yet does not win by dividing by ~0);
  • the current desk has no open position on the real account (web/public_real.json «sig» = «<desk>:<id>»; a position without
    «sig» counts as the current desk's; the file must be fresher than 2 hours) — an open position is never moved between desks;
  • at least 7 days have passed since the last switch (the owner's choice counts as a switch).
28.10 (owner's decisions on the data agent's proposals, real money) — two more conditions:
  • READY: a challenger may become the main desk only when the strategy conveyor (web/conveyor.json) has moved it to READY —
    ≥ 30 closed trades, ≥ 21 days, profit factor ≥ 1.2, average trade ≥ +0.3 % after costs (and the conveyor's own drawdown and
    t-test gates); the numbers are checked here too, so a missing or stale conveyor record never lets a desk through;
  • families, not sizes: «Консервативная», «Сбалансированная» and «Агрессивная» are ONE family («Автопилот») — the same signals at
    three sizes. The selector compares families; the family is represented by the size the owner chose by hand on 28.10
    («Агрессивная», FAMILY_SIZE) — or by the family member that is the main desk now — and never switches between its sizes.
Every run writes web/desk_select.json:
  {"current", "name", "since", "by": "owner" | "auto", "candidates": [{"key", "name", "trades", "result_pct", "dd_pct", "ratio",
   "cur_ratio" (the current desk's ratio over the same window), "family", "members", "ready", "eligible", "why"}],
   "family", "family_ru" (the current desk's family), "decision": "hold" | "wait" | "switch",
   "why_ru", "short_ru" (the panel's «автовыбор: …»), "asof"};
  30.10: plus "watch": [{"key": "whales", … trades, days, pf, mean_pct, dd_pct, ready, ready_why, eligible: false}] — desks shown
  against the READY rule for the owner only (monitoring); nothing in it can be chosen.
a switch also appends one line to data/desk_select.jsonl and prints it. The previous web/desk_select.json is the state (current,
since, by); missing or broken → the owner's choice. Readers (panel_header.main_desk) fall back to «Агрессивная» on a missing or
broken file; OKX_FUT_PROFILES on the autopilot still overrides everything. Demo numbers only; no orders here.

30.10 (owner, real money, his numbers: «3 дня и 5 сделок — 5% бюджета, 7 дней и 10 сделок — 15%, 14 дней и 15 сделок — полная
доля… можем попробовать»): STAGED PROMOTION replaces the READY 30/21 gate as the way a desk earns real money. Every hour each
staged candidate (a family counts as one; «Киты» from web/whales_top.json and «Золото» from web/gold_desk.json too) is measured
on its window — from its start, or from its last rollback — and climbs one step at a time:
  ступень 1: ≥ 3 days live, ≥ 5 closed trades, result > 0, PF ≥ 1,3                → 5 % of the futures budget;
  ступень 2: ≥ 7 days, ≥ 10 trades, still > 0, PF ≥ 1,3, drawdown ≤ 10 %          → 15 %;
  ступень 3: ≥ 14 days, ≥ 15 trades (and the stage-2 numbers)                      → the full share: an equal part with the main desk.
Rollback at once, one step down (to 0 from stage 1): 5 consecutive UTC days with closed trades all in the red, or the drawdown
deeper than the desk's own max drawdown at its last promotion + 3 points, or PF < 1 over the last 10 trades. After a rollback
the window restarts (days and trades count from the rollback), so re-promotion needs the stage test passed AGAIN. The sum of the
stage-1/2 shares ≤ 40 % of the futures budget (a promotion that would break it waits); «Агрессивная» (the main desk, + «Импульс
BTC», + «Сигналы» after their gate, + stage-3 desks) keeps the remainder in equal parts. Only desks of web/paper.json can be
followed by the real OKX sleeve (okx_futures.pick_desks reads «stages.alloc»); a staged «Киты» or «Золото» keeps its stage but its
share stays with the main desks until an executor exists («real»: false) — and gold needs the owner's separate OK anyway
(«eligible_real»: false). The old main-desk switch now needs the challenger at stage 3 instead of READY (the rest unchanged).
Every change writes a line to data/desk_select.jsonl and to «stages.log» of web/desk_select.json. Stdlib only."""
from __future__ import annotations

import json
import os
import time

from . import conveyor as cv
from . import panel_header as ph

FEED = ph.SEL_FILE                     # web/desk_select.json
LOG = os.path.join("data", "desk_select.jsonl")
DAY = 86_400_000
SECOND = ph.NOVA_BASE[1]               # «Импульс BTC» — always the second half, never the main desk
NEVER = ("lab", "brk")                 # the owner: never the main desk (experiment / failed check)
SPOT = ("imps",)                       # spot desks are not futures
EV_OK = ("partial", "passed", "oos")   # the history check: partial or stronger
MIN_TRADES = 20
BEAT = 0.20                            # the challenger's result ÷ drawdown must beat the current desk's by ≥ 20 %
DD_FLOOR = 1.0                         # % — the drawdown a ratio divides by is never below this
MIN_GAP_MS = 7 * DAY                   # ≥ 7 days between switches
REAL_FRESH_MS = 2 * 3_600_000          # public_real.json older than this does not prove «no open position»
OWNER = {"current": "aggr", "since": 1793164774000, "by": "owner"}   # 28.10 07:19 Kyiv — commit 036f1bb09 (owner: «переключайся»)
# 28.10 (owner): the conveyor's READY gate is required too (conveyor.READY_* — the same numbers, checked again here)
READY_TRADES, READY_DAYS, READY_PF, READY_MEAN = cv.READY_MIN_TRADES, cv.READY_MIN_DAYS, cv.READY_MIN_PF, cv.READY_MIN_MEAN_PCT
# 28.10 (owner): one family of three sizes — compared as one candidate; its size is the owner's hand choice of 28.10
FAMILIES = {"autopilot": ("cons", "bal", "aggr")}
FAMILY_SIZE = {"autopilot": "aggr"}
FAMILY_RU = {"autopilot": "Автопилот"}
FAMILY_ABOUT_RU = {"autopilot": "«Консервативная», «Сбалансированная» и «Агрессивная» — одни и те же сигналы разного размера; "
                                "размер — «Агрессивная» (выбор владельца 28.10)"}


def _num(x, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _load(out: str, name: str):
    try:
        with open(os.path.join(out, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _ddmm(ms: int) -> str:
    import datetime as dt
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Kyiv")
    except Exception:  # noqa: BLE001
        tz = dt.timezone(dt.timedelta(hours=2))
    return dt.datetime.fromtimestamp(ms / 1000, tz).strftime("%d.%m")


def _g(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _f(x: float, nd: int = 2) -> str:
    return f"{x:+.{nd}f}".replace(".", ",").replace("-", "−")


# ── candidates ───────────────────────────────────────────────────────────────────────────────────────────────────────
def candidate_keys(prof: dict | None = None) -> list[str]:
    """the desks the selector may pick as the MAIN desk: futures desks whose history check is not failed (conveyor.DESKS ev) —
    with `prof` only those present in web/paper.json (and not paper-only)"""
    out = []
    for k, d in cv.DESKS.items():
        if k in NEVER or k in SPOT or k == SECOND or d.get("ev") not in EV_OK or d.get("holding") or d.get("mirror_of"):
            continue
        if prof is not None and (not isinstance(prof.get(k), dict) or prof[k].get("paper_only")):
            continue
        out.append(k)
    return out


def start_of(v: dict) -> int:
    """the desk's first record (ms) — its part of the window starts there when it began after 17.10"""
    h = v.get("equity_history") or []
    return int(_num(h[0].get("time"), ph.FUT_D0)) if h else int(_num(v.get("since"), ph.FUT_D0))


def base_at(v: dict, t0: int) -> float:
    """the desk's equity at t0 (its last record at or before t0; its start equity when it began later) — ph.base_d0 for any t0"""
    h = v.get("equity_history") or []
    st = _num(v.get("start_equity"), 0.0) or ph.START
    if not h or _num(h[0].get("time")) >= t0:
        return st
    b = h[0].get("equity")
    for e in h:
        if _num(e.get("time")) <= t0:
            b = e.get("equity")
        else:
            break
    return _num(b, 0.0) or st


def trades_since(v: dict, t0: int = ph.FUT_D0) -> int:
    """closed trades since t0; paper.json keeps only the last 50 — a desk that started after t0 has them all in stats.trades"""
    n = sum(1 for c in v.get("closed") or [] if _num(c.get("closed")) >= t0)
    return max(n, int(_num((v.get("stats") or {}).get("trades")))) if start_of(v) >= t0 else n


def metrics(v: dict, t0: int) -> dict:
    """result % and max drawdown % of one desk from t0 to now (open trades at their desk price, as the header counts them)"""
    b = base_at(v, t0)
    now_eq = ph.fut_eq(v)
    vals = [b] + [_num(e.get("equity")) for e in v.get("equity_history") or [] if _num(e.get("time")) > t0] + [now_eq]
    res = (now_eq / b - 1) * 100 if b else 0.0
    dd = cv._max_dd(vals)
    return {"result_pct": round(res, 2), "dd_pct": round(dd, 2), "ratio": round(res / max(dd, DD_FLOOR), 3)}


def family_of(k: str) -> str:
    """the desk's family key: «autopilot» for cons / bal / aggr, the desk itself otherwise"""
    return next((f for f, ks in FAMILIES.items() if k in ks), k)


def ready_check(stage: dict | None) -> tuple[bool, str]:
    """(READY, why not). 30.10: a staged-promotion record ({"stage": 0–3, …}, see stage_eval) is READY at stage 3 — the full
    share; a conveyor record (the old gate) needs its stage «ready» AND its numbers; no record → not ready"""
    s = stage or {}
    if isinstance(s.get("stage"), int):
        if s["stage"] >= 3:
            return True, ""
        conv_ok, conv_why = ready_check(s.get("conv")) if s.get("conv") else (False, "")
        if conv_ok:                                     # the old gate still counts: READY by the conveyor is READY
            return True, ""
        return False, f"ступень {s['stage']} из 3 — нужна полная доля (ступень 3)"
    miss = []
    if s.get("stage") != "ready":
        miss.append(f"конвейер: стадия «{s.get('stage') or 'нет данных'}», нужна READY")
    n, d, pf, mean = _num(s.get("trades")), _num(s.get("days")), s.get("pf"), s.get("mean_pct")
    if n < READY_TRADES:
        miss.append(f"сделок {int(n)} из {READY_TRADES}")
    if d < READY_DAYS:
        miss.append(f"дней {d:.1f} из {READY_DAYS}".replace(".", ","))
    if pf is None or _num(pf) < READY_PF:
        miss.append(f"профит-фактор {'—' if pf is None else f'{_num(pf):.2f}'.replace('.', ',')} (нужно от {_g(READY_PF)})")
    if mean is None or _num(mean) < READY_MEAN:
        miss.append(f"средняя сделка {'—' if mean is None else _f(_num(mean)) + '%'} (нужно от +{_g(READY_MEAN)}%)")
    return not miss, "; ".join(miss)


def family_reps(keys: list, cur: str) -> list:
    """one desk per family: the current desk for its own family, else the family's size (FAMILY_SIZE), else its first member"""
    out, seen = [], set()
    for k in keys:
        f = family_of(k)
        if f in seen:
            continue
        seen.add(f)
        members = [x for x in FAMILIES.get(f, (k,)) if x in keys]
        rep = cur if cur in members else FAMILY_SIZE.get(f) if FAMILY_SIZE.get(f) in members else members[0]
        out.append(rep)
    return out


# ── the real account ─────────────────────────────────────────────────────────────────────────────────────────────────
def real_holds(real, desk: str, now: int) -> tuple[bool | None, str]:
    """(True / False = does the real account hold a futures position of `desk`; None = unknown), and why"""
    if not isinstance(real, dict) or not isinstance(real.get("futures"), list):
        return None, "нет данных реального счёта (web/public_real.json)"
    if now - int(_num(real.get("asof"))) > REAL_FRESH_MS:
        return None, "данные реального счёта старше 2 часов"
    mine = [p for p in real["futures"] if isinstance(p, dict) and (not p.get("sig") or str(p["sig"]).split(":")[0] == desk)]
    if mine:
        return True, f"на реальном счёте открыта позиция стола ({', '.join(str(p.get('inst') or '?') for p in mine[:3])})"
    return False, "на реальном счёте нет открытых позиций этого стола"


# ── the decision ─────────────────────────────────────────────────────────────────────────────────────────────────────
def state_of(prev) -> dict:
    """the current desk, since when and by whom — from the last web/desk_select.json; missing / broken → the owner's 28.10 choice"""
    ok = isinstance(prev, dict) and prev.get("current") in candidate_keys() and prev.get("current") in ph.MAIN_KEYS \
        and isinstance(prev.get("since"), (int, float)) and prev.get("by") in ("owner", "auto")       # (a desk missing from one
    # paper.json does not reset the choice)
    return {"current": prev["current"], "since": int(prev["since"]), "by": prev["by"]} if ok else dict(OWNER)


def decide(prof: dict, prev, real, stages: dict | None, now: int) -> dict:
    st = state_of(prev)
    cur = st["current"]
    name = lambda k: (prof.get(k) or {}).get("name") or k                                  # noqa: E731
    vc = prof.get(cur) or {}
    stages = stages or {}
    cands = []
    cur_fam = family_of(cur)
    for k in family_reps(candidate_keys(prof), cur):
        v = prof[k]
        fam = family_of(k)
        t0 = max(ph.FUT_D0, start_of(v), start_of(vc) if vc else ph.FUT_D0)
        m = metrics(v, t0)
        mc = metrics(vc, t0) if vc else {"ratio": 0.0}
        n = trades_since(v)
        exp_dd = cv.expected_dd(k)
        why = []
        ready, why_ready = ready_check(stages.get(k))
        if k == cur:
            why.append("текущий основной стол")
        elif fam == cur_fam:
            why.append("то же семейство, что и текущий стол — размеры не переключаем")
        else:
            if not ready:
                why.append(why_ready)
            if n < MIN_TRADES:
                why.append(f"мало сделок: {n} из {MIN_TRADES}")
            dd_live = max(m["dd_pct"], _num((v.get("stats") or {}).get("max_dd_pct")))   # the desk's own (finer) record, if deeper
            if dd_live > exp_dd:
                why.append(f"просадка {dd_live:.1f}% глубже ожидаемой по истории {exp_dd:g}%".replace(".", ","))
            if (stages.get(k) or {}).get("stage") == "retired" or (stages.get(k) or {}).get("paused"):
                why.append("снят конвейером")
            need = mc["ratio"] + BEAT * abs(mc["ratio"])
            if m["ratio"] <= 0:
                why.append(f"результат к просадке {_f(m['ratio'])} — не в плюсе")
            elif m["ratio"] < need:
                why.append(f"результат к просадке {_f(m['ratio'])} против {_f(mc['ratio'])} у текущего — нужно на 20% лучше")
        cands.append({"key": k, "name": name(k), "trades": n, **m, "cur_ratio": mc["ratio"], "exp_dd_pct": exp_dd,
                      "family": fam, "family_ru": FAMILY_RU.get(fam), "members": list(FAMILIES.get(fam, (k,))),
                      "ready": ready, "ready_why": why_ready or "READY по конвейеру",
                      "eligible": not why, "why": "; ".join(why) if why else "все условия выполнены"})
    best = max((c for c in cands if c["eligible"]), key=lambda c: c["ratio"], default=None)
    others = [c for c in cands if c["key"] != cur]
    doc = {"current": cur, "name": name(cur), "since": st["since"], "by": st["by"], "candidates": cands, "asof": now,
           "family": cur_fam, "family_ru": (f"{FAMILY_RU[cur_fam]}: {FAMILY_ABOUT_RU[cur_fam]}" if cur_fam in FAMILY_RU else None),
           "rules_ru": (f"Претендент должен быть READY по конвейеру ({READY_TRADES} сделок, {READY_DAYS} дн., профит-фактор от "
                        f"{_g(READY_PF)}, средняя сделка от +{_g(READY_MEAN)}%), иметь от {MIN_TRADES} сделок с 17.10, просадку в пределах "
                        "истории и результат к просадке на 20% лучше текущего; не чаще раза в 7 дней и не при открытой позиции. "
                        "Сравниваются семейства, а не размеры одного семейства (решение владельца 28.10).")}
    if best is None:
        if others and not any(c["ready"] for c in others):
            short = "ждёт READY конвейера"
            why = (f"Держим «{name(cur)}»: ни у одного другого семейства нет готовности по конвейеру ({READY_TRADES} сделок, "
                   f"{READY_DAYS} дн., профит-фактор от {_g(READY_PF)}, средняя сделка от +{_g(READY_MEAN)}%) — "
                   + "; ".join(f"«{c['name']}»: {c['ready_why']}" for c in others) + ".")
        elif others and all(c["trades"] < MIN_TRADES for c in others):
            mx = max(c["trades"] for c in others)
            short = f"ждёт {MIN_TRADES} сделок"
            why = (f"Держим «{name(cur)}»: у других столов меньше {MIN_TRADES} закрытых сделок с 17.10 (больше всех — {mx}); "
                   "раньше сравнивать нечестно.")
        else:
            short = "лучше по результату к просадке нет"
            why = f"Держим «{name(cur)}»: ни один стол не проходит все условия — " + \
                  "; ".join(f"«{c['name']}»: {c['why']}" for c in others) + "."
        return {**doc, "decision": "hold", "why_ru": why, "short_ru": short}
    gap = now - st["since"]
    if gap < MIN_GAP_MS:
        days = f"{gap / DAY:.1f}".replace(".", ",")
        return {**doc, "decision": "wait", "short_ru": f"«{best['name']}» лучше, переключение не раньше {_ddmm(st['since'] + MIN_GAP_MS)}",
                "why_ru": f"«{best['name']}» проходит все условия (результат к просадке {_f(best['ratio'])} против {_f(best['cur_ratio'])}), "
                          f"но с последнего переключения прошло {days} дн. из 7 — ждём."}
    held, why_real = real_holds(real, cur, now)
    if held is not False:
        return {**doc, "decision": "wait", "short_ru": f"на «{best['name']}» — после закрытия позиции" if held else "ждёт данных реального счёта",
                "why_ru": f"«{best['name']}» проходит все условия, но {why_real}: позицию между столами не переносим — ждём."}
    ddt = f"{best['dd_pct']:.1f}".replace(".", ",")
    why = (f"Переключено с «{name(cur)}» на «{best['name']}» (READY по конвейеру): {best['trades']} сделок с 17.10, результат к просадке {_f(best['ratio'])} "
           f"против {_f(best['cur_ratio'])} (лучше на 20% и больше), просадка {ddt}% в пределах истории {best['exp_dd_pct']:g}%, "
           f"{why_real}.")
    return {**doc, "current": best["key"], "name": best["name"], "since": now, "by": "auto", "decision": "switch", "from": cur,
            "why_ru": why, "short_ru": f"переключил с «{name(cur)}» {_ddmm(now)}"}


# ── watch list (30.10, owner: «Киты … давайте думать, как её быстро включать») ──────────────────────────────────────
# Desks that are NOT candidates (no history check, not in web/paper.json, not covered by the conveyor) but whose distance to the
# approved READY rule the panel shows, so the owner sees the progress. Monitoring only: nothing here can become the main desk —
# state_of / candidate_keys / decide never read it. Switching such a desk on is the owner's explicit decision, not this file's.
WATCH_WHY = "наблюдение: не кандидат автовыбора — нет проверки на истории и конвейера; включение только решением владельца"


def watch_whales(wtop, now: int) -> dict | None:
    """«Киты · копирование топ-5» (web/whales_top.json desk) against the READY numbers: closed trades, days live, profit factor,
    mean trade in % of the position's notional (as the conveyor counts it), max drawdown of the equity curve"""
    d = (wtop or {}).get("desk") if isinstance(wtop, dict) else None
    if not isinstance(d, dict) or not d.get("started"):
        return None
    cl = [t for t in d.get("closed") or [] if isinstance(t, dict)]
    pn = [_num(t.get("pnl")) for t in cl]
    gp, gl = sum(x for x in pn if x > 0), -sum(x for x in pn if x < 0)
    pf = (gp / gl) if gl > 0 else (None if not pn else 99.0)
    means = [_num(t.get("pnl")) / _num(t.get("notional")) * 100 for t in cl if _num(t.get("notional")) > 0]
    mean = sum(means) / len(means) if means else None
    hist = d.get("history") or []
    vals = [_num(d.get("start"), ph.START)] + [_num(e[1] if isinstance(e, (list, tuple)) else (e or {}).get("equity")) for e in hist] \
        + [_num(d.get("equity"))]
    dd = max(cv._max_dd(vals), _num(d.get("max_dd_pct")))
    n, days = len(cl) if not d.get("trades") else int(_num(d.get("trades"))), (now - int(_num(d.get("started")))) / DAY
    stage = {"stage": "watch", "trades": n, "days": days, "pf": pf, "mean_pct": mean}
    ready, why_ready = ready_check(stage)
    why_ready = why_ready.replace("конвейер: стадия «watch», нужна READY", "вне конвейера (нет проверки на истории)")
    base = _num(d.get("start"), ph.START)
    return {"key": "whales", "name": d.get("name_ru") or "Киты · копирование топ-5", "trades": n, "days": round(days, 1),
            "pf": None if pf is None else round(pf, 2), "mean_pct": None if mean is None else round(mean, 2),
            "result_pct": round((_num(d.get("equity")) / base - 1) * 100, 2) if base else None, "dd_pct": round(dd, 2),
            "since": int(_num(d.get("started"))), "open": len(d.get("open") or []), "ready": ready,
            "ready_why": why_ready or "READY по числам, но вне конвейера", "eligible": False, "why": WATCH_WHY,
            "whales": sorted({str(t.get("whale") or "") for t in cl} - {""}), "coins": sorted({str(t.get("coin") or "") for t in cl} - {""})}


# ── staged promotion (30.10, owner: «3 дня и 5 сделок — 5%, 7 дней и 10 сделок — 15%, 14 дней и 15 сделок — полная доля») ─────
STAGE_NEED = {1: (3, 5), 2: (7, 10), 3: (14, 15)}        # stage → (days live, closed trades) on the desk's window
STAGE_SHARE = {0: 0.0, 1: 0.05, 2: 0.15}                 # stage 3 = the full share (an equal part with the main desk)
STAGE_PF = 1.3                                            # profit factor on the window, every stage
STAGE_DD = 10.0                                           # % — max drawdown on the window, stages 2 and 3
STAGE_CAP = 0.40                                          # the stage-1/2 shares together never exceed this part of the futures budget
ROLL_LOSING_DAYS = 5                                      # UTC days in a row with closed trades all summing below 0 → one step down
ROLL_DD_PLUS = 3.0                                        # points over the desk's own max drawdown at its last promotion → one step down
ROLL_LAST_N = 10                                          # PF < 1 over the last 10 trades → one step down
EXTRA = ("whales", "gold")                                # staged candidates outside web/paper.json (their own feeds)
EXTRA_NAME = {"whales": "Киты · копирование топ-5", "gold": "Золото · тест"}
STAGE_RULES_RU = ("Ступени (решение владельца 30.10): 1 — от 3 дней и 5 сделок, результат в плюсе, профит-фактор от 1,3 → 5% бюджета "
                  "фьючерсов; 2 — от 7 дней и 10 сделок, просадка до 10% → 15%; 3 — от 14 дней и 15 сделок → полная доля наравне с "
                  "основным столом. Откат на ступень вниз сразу: 5 убыточных дней подряд, просадка глубже своей прошлой максимальной "
                  "на 3 пункта или профит-фактор ниже 1 по последним 10 сделкам; после отката отсчёт дней и сделок начинается заново. "
                  "Сумма долей ступеней 1–2 — не больше 40% бюджета; остаток — у «Агрессивной» и столов на ступени 3 поровну. "
                  "Семейство «Консервативная / Сбалансированная / Агрессивная» — один стол.")


def _pf(nets: list) -> float | None:
    gp, gl = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
    return (gp / gl) if gl > 0 else (99.0 if gp > 0 else None)


def losing_days(trades: list, now: int) -> int:
    """UTC calendar days in a row, newest first (ending today or yesterday), each WITH closed trades summing below 0; a day without
    a closed trade ends the count. trades = [(closed ms, net usd)]"""
    by = {}
    for t, net in trades:
        by[int(t) // DAY] = by.get(int(t) // DAY, 0.0) + net
    d = int(now) // DAY
    if d not in by:
        d -= 1
    n = 0
    while d in by and by[d] < 0:
        n += 1
        d -= 1
    return n


def stage_source(key: str, prof: dict, wtop, gold) -> dict | None:
    """one staged candidate as {name, start, trades [(closed, net)], curve [(t, eq)], eq_now, start_eq, dd_record, real, eligible_real}
    — a web/paper.json desk, «Киты» (whales_top.json → desk) or «Золото» (gold_desk.json); None when the source is missing"""
    if key == "whales":
        d = (wtop or {}).get("desk") if isinstance(wtop, dict) else None
        if not isinstance(d, dict) or not d.get("started"):
            return None
        cl = [t for t in d.get("closed") or [] if isinstance(t, dict)]
        return {"key": key, "name": d.get("name_ru") or EXTRA_NAME[key], "start": int(_num(d.get("started"))),
                "trades": [(int(_num(t.get("closed_t"))), _num(t.get("pnl"))) for t in cl], "n_all": int(_num(d.get("trades"), len(cl))),
                "curve": [(int(_num(e[0] if isinstance(e, (list, tuple)) else (e or {}).get("t"))),
                           _num(e[1] if isinstance(e, (list, tuple)) else (e or {}).get("equity"))) for e in d.get("history") or []],
                "eq_now": _num(d.get("equity"), ph.START), "start_eq": _num(d.get("start"), ph.START), "dd_record": _num(d.get("max_dd_pct")),
                "real": False, "real_why": "сделки «Китов» реальный счёт пока не повторяет — нет исполнителя; доля ступени остаётся у основных столов",
                "eligible_real": True, "eligible_real_why": None,
                "note": (f"все сделки — {len({str(t.get('whale')) for t in cl})} кит(а)" if cl else None)}
    if key == "gold":
        d = gold if isinstance(gold, dict) and gold.get("since") else None
        if not d:
            return None
        v = d
        return {"key": key, "name": v.get("name") or EXTRA_NAME[key], "start": int(_num(v.get("since"))),
                "trades": [(int(_num(c.get("closed"))), _num(c.get("net_usd"))) for c in v.get("closed") or [] if c.get("closed")],
                "n_all": int(_num((v.get("stats") or {}).get("trades"))),
                "curve": [(int(_num(e.get("time"))), _num(e.get("equity"))) for e in v.get("equity_history") or []],
                "eq_now": _num(v.get("equity_now"), _num(v.get("equity"), ph.START)), "start_eq": _num(v.get("start_equity"), ph.START),
                "dd_record": _num((v.get("stats") or {}).get("max_dd_pct")), "real": False,
                "real_why": "золото реальный счёт не торгует: нет исполнителя и нужно отдельное решение владельца",
                "eligible_real": False, "eligible_real_why": v.get("eligible_real_why") or "нужно отдельное решение владельца", "note": None}
    v = prof.get(key)
    if not isinstance(v, dict):
        return None
    return {"key": key, "name": v.get("name") or key, "start": start_of(v),
            "trades": [(int(_num(c.get("closed"))), _num(c.get("net_usd"))) for c in v.get("closed") or [] if c.get("closed")],
            "n_all": int(_num((v.get("stats") or {}).get("trades"))),
            "curve": [(int(_num(e.get("time"))), _num(e.get("equity"))) for e in v.get("equity_history") or []],
            "eq_now": ph.fut_eq(v), "start_eq": _num(v.get("start_equity"), 0.0) or ph.START,
            "dd_record": _num((v.get("stats") or {}).get("max_dd_pct")), "real": True, "real_why": None,
            "eligible_real": True, "eligible_real_why": None, "note": None}


def stage_metrics(src: dict, t0: int, now: int) -> dict:
    """the stage numbers of one desk on [t0, now]: days, trades, result %, PF, max drawdown %, PF of the last 10 trades, losing days"""
    curve = [(t, e) for t, e in src["curve"] if e > 0]
    base = src["start_eq"]
    for t, e in curve:
        if t <= t0:
            base = e
        else:
            break
    vals = [base] + [e for t, e in curve if t > t0] + [src["eq_now"]]
    tr = sorted((t, n) for t, n in src["trades"] if t >= t0)
    n = len(tr)
    if src["start"] >= t0:                                          # the feed keeps only the last trades: the desk's own count is fuller
        n = max(n, src["n_all"])
    nets = [x for _, x in tr]
    dd = cv._max_dd(vals)
    if src["start"] >= t0:
        dd = max(dd, src["dd_record"])
    pf10 = _pf(nets[-ROLL_LAST_N:]) if len(nets) >= ROLL_LAST_N else None
    return {"days": round(max(0.0, (now - max(t0, src["start"])) / DAY), 1), "trades": n,
            "result_pct": round((src["eq_now"] / base - 1) * 100, 2) if base else 0.0, "pf": None if _pf(nets) is None else round(_pf(nets), 2),
            "dd_pct": round(dd, 2), "pf_last10": None if pf10 is None else round(pf10, 2), "losing_days": losing_days(tr, now)}


def stage_test(level: int, m: dict) -> list:
    """what the desk still lacks for `level` (empty = passes)"""
    need_d, need_n = STAGE_NEED[level]
    miss = []
    if m["days"] < need_d:
        miss.append(f"дней {m['days']:g} из {need_d}".replace(".", ","))
    if m["trades"] < need_n:
        miss.append(f"сделок {m['trades']} из {need_n}")
    if m["result_pct"] <= 0:
        miss.append(f"результат {_f(m['result_pct'])}% — не в плюсе")
    if m["pf"] is None or m["pf"] < STAGE_PF:
        miss.append(f"профит-фактор {'—' if m['pf'] is None else _g(round(m['pf'], 2))} (нужно от {_g(STAGE_PF)})")
    if level >= 2 and m["dd_pct"] > STAGE_DD:
        miss.append(f"просадка {_g(round(m['dd_pct'], 1))}% (нужно до {_g(STAGE_DD)}%)")
    return miss


def rollback_why(st: dict, m: dict) -> str | None:
    if m["losing_days"] >= ROLL_LOSING_DAYS:
        return f"{m['losing_days']} убыточных дней подряд"
    ref = st.get("dd_ref")
    if isinstance(ref, (int, float)) and m["dd_pct"] > ref + ROLL_DD_PLUS:
        return f"просадка {_g(round(m['dd_pct'], 1))}% глубже прошлой максимальной {_g(round(ref, 1))}% на {_g(ROLL_DD_PLUS)} пункта"
    if m["pf_last10"] is not None and m["pf_last10"] < 1.0:
        return f"профит-фактор {_g(m['pf_last10'])} по последним {ROLL_LAST_N} сделкам"
    return None


def stage_keys(prof: dict, cur: str) -> list:
    """the staged candidates: one per family of the main-desk candidates except the current desk's family, + «Киты» and «Золото»"""
    cur_fam = family_of(cur)
    return [k for k in family_reps(candidate_keys(prof), cur) if family_of(k) != cur_fam] + list(EXTRA)


def stage_eval(prof: dict, prev, wtop, gold, cur: str, now: int) -> dict:
    """the hourly step of the ladder: {desks: [...], alloc: {key: share}, staged_pct, main_pct, cap_pct, log: [...], rules_ru}.
    prev = the last web/desk_select.json (its «stages.desks» is the state)"""
    old = {d.get("key"): d for d in ((prev or {}).get("stages") or {}).get("desks") or [] if isinstance(d, dict) and d.get("key")} \
        if isinstance(prev, dict) else {}
    desks, log = [], []
    for k in stage_keys(prof, cur):
        src = stage_source(k, prof, wtop, gold)
        if src is None:
            continue
        st = old.get(k) or {}
        stage = int(_num(st.get("stage"))) if st.get("stage") in (0, 1, 2, 3) else 0
        t_reset = int(_num(st.get("t_reset"))) if st.get("t_reset") else 0
        t0 = max(ph.FUT_D0, src["start"], t_reset)
        m = stage_metrics(src, t0, now)
        since = int(_num(st.get("since"), now)) if st else now
        dd_ref = st.get("dd_ref")
        change = None
        why = rollback_why(st, m) if stage > 0 else None
        if why:
            stage, since, t_reset, dd_ref = stage - 1, now, now, m["dd_pct"]
            change = {"t": now, "key": k, "name": src["name"], "from": stage + 1, "to": stage, "why_ru": f"откат: {why}; отсчёт дней и сделок заново"}
            t0 = max(ph.FUT_D0, src["start"], t_reset)
            m = stage_metrics(src, t0, now)
        desks.append({"key": k, "name": src["name"], "stage": stage, "since": since, "t_reset": t_reset or None, "dd_ref": dd_ref,
                      **m, "window_from": t0, "real": src["real"], "real_why": src["real_why"],
                      "eligible_real": src["eligible_real"], "eligible_real_why": src["eligible_real_why"], "note": src["note"],
                      "family": family_of(k), "members": list(FAMILIES.get(family_of(k), (k,))), "_change": change})
    # promotions, one step each, best-staged first, within the 40 % cap on the stage-1/2 shares
    desks.sort(key=lambda d: (-d["stage"], d["since"]))
    used = sum(STAGE_SHARE.get(d["stage"], 0.0) for d in desks)
    for d in desks:
        if d["_change"]:
            d["next_ru"] = d["_change"]["why_ru"]
            continue
        nxt = d["stage"] + 1
        if nxt > 3:
            d["next_ru"] = "полная доля — выше ступени нет"
            continue
        miss = stage_test(nxt, d)
        if miss:
            d["next_ru"] = f"до ступени {nxt}: " + ", ".join(miss)
            continue
        add = STAGE_SHARE.get(nxt, 0.0) - STAGE_SHARE.get(d["stage"], 0.0)
        if used + add > STAGE_CAP + 1e-9:
            d["next_ru"] = f"ступень {nxt} готова, ждёт места: сумма долей ступеней {_g(round(used * 100))}% + {_g(round(add * 100))}% больше {_g(round(STAGE_CAP * 100))}%"
            continue
        used += add
        d["_change"] = {"t": now, "key": d["key"], "name": d["name"], "from": d["stage"], "to": nxt,
                        "why_ru": (f"ступень {nxt}: {str(d['days']).replace('.', ',')} дн., {d['trades']} сделок, результат {_f(d['result_pct'])}%, "
                                   f"профит-фактор {_g(d['pf'])}, просадка {_g(round(d['dd_pct'], 1))}%")}
        d["stage"], d["since"], d["dd_ref"] = nxt, now, d["dd_pct"]
        d["next_ru"] = "повышена сейчас: " + d["_change"]["why_ru"]
    for d in desks:
        d["share"] = STAGE_SHARE.get(d["stage"]) if d["stage"] < 3 else None
        d["share_ru"] = "полная доля" if d["stage"] == 3 else f"{_g(round(STAGE_SHARE[d['stage']] * 100))}%"
        if d["_change"]:
            log.append(d.pop("_change"))
        else:
            d.pop("_change")
    staged = sum(STAGE_SHARE.get(d["stage"], 0.0) for d in desks if d["stage"] in (1, 2))
    return {"rules_ru": STAGE_RULES_RU, "cap_pct": STAGE_CAP * 100, "desks": desks, "staged_pct": round(staged * 100, 2),
            "main_pct": round((1 - staged) * 100, 2), "log": log}


def alloc_shares(prof: dict, cur: str, stages: dict) -> dict:
    """the real futures sleeve's budget shares {desk key: fraction, sum 1}: stage-1/2 desks the sleeve can follow (web/paper.json)
    at their share; the main set (the main desk + «Импульс BTC» + «Сигналы» after their gate) and the followable stage-3 desks share
    the rest equally. A staged desk the sleeve cannot follow («Киты», «Золото») leaves its share with the main set."""
    main = list(ph.nova_keys(prof, {"current": cur}))
    out, used = {}, 0.0
    for d in stages.get("desks") or []:
        if not d.get("real") or d["key"] in main:
            continue
        if d["stage"] in (1, 2):
            out[d["key"]] = STAGE_SHARE[d["stage"]]
            used += STAGE_SHARE[d["stage"]]
        elif d["stage"] == 3:
            main.append(d["key"])
    for k in main:
        out[k] = round((1 - used) / len(main), 6)
    return out


def run(out: str = "web", now: int | None = None, log_path: str = LOG) -> dict:
    """one engine step: read the desks, the last decision and the real account → web/desk_select.json (+ a log line on a switch)"""
    now = int(now or time.time() * 1000)
    paper = _load(out, "paper.json") or {}
    prof = paper.get("profiles") or {}
    if not prof:
        raise RuntimeError("web/paper.json: нет столов — выбор не пересчитан")
    prev = _load(out, FEED)
    wtop, gold = _load(out, "whales_top.json"), _load(out, "gold_desk.json")
    cur = state_of(prev)["current"]
    stg = stage_eval(prof, prev, wtop, gold, cur, now)                 # 30.10: the ladder (its state = the previous file)
    conv = _load(out, "conveyor.json") or {}
    convd = {d.get("key"): d for d in conv.get("desks") or [] if isinstance(d, dict) and d.get("key")}
    stages = {d["key"]: {**d, "conv": convd.get(d["key"])} for d in stg["desks"]}   # READY = stage 3 of the ladder OR the conveyor's gate
    for k, c in convd.items():
        stages.setdefault(k, c)
    doc = decide(prof, prev, _load(out, "public_real.json"), stages, now)
    stg["alloc"] = alloc_shares(prof, doc["current"], stg)
    stg["short_ru"] = " · ".join(f"«{d['name']}» ступень {d['stage']} ({d['share_ru']})" for d in stg["desks"]) or "кандидатов нет"
    doc["stages"] = stg
    try:
        w = watch_whales(wtop, now)
    except Exception as e:  # noqa: BLE001 — the watch list never breaks the decision
        print("desk_select watch:", e, flush=True)
        w = None
    doc["watch"] = [w] if w else []
    lines = [{"kind": "stage", **x} for x in stg["log"]]
    if doc["decision"] == "switch":
        lines.append({"kind": "switch", "t": now, "from": doc["from"], "to": doc["current"], "why_ru": doc["why_ru"]})
    for line in lines:
        print("desk_select:", json.dumps(line, ensure_ascii=False), flush=True)
        try:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as e:
            print("desk_select log:", e, flush=True)
    tmp = os.path.join(out, FEED + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(out, FEED))
    return doc

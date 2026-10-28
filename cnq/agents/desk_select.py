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
Every run writes web/desk_select.json:
  {"current", "name", "since", "by": "owner" | "auto", "candidates": [{"key", "name", "trades", "result_pct", "dd_pct", "ratio",
   "cur_ratio" (the current desk's ratio over the same window), "eligible", "why"}], "decision": "hold" | "wait" | "switch",
   "why_ru", "short_ru" (the panel's «автовыбор: …»), "asof"};
a switch also appends one line to data/desk_select.jsonl and prints it. The previous web/desk_select.json is the state (current,
since, by); missing or broken → the owner's choice. Readers (panel_header.main_desk) fall back to «Агрессивная» on a missing or
broken file; OKX_FUT_PROFILES on the autopilot still overrides everything. Demo numbers only; no orders here. Stdlib only."""
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
    for k in candidate_keys(prof):
        v = prof[k]
        t0 = max(ph.FUT_D0, start_of(v), start_of(vc) if vc else ph.FUT_D0)
        m = metrics(v, t0)
        mc = metrics(vc, t0) if vc else {"ratio": 0.0}
        n = trades_since(v)
        exp_dd = cv.expected_dd(k)
        why = []
        if k == cur:
            why.append("текущий основной стол")
        else:
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
                      "eligible": not why, "why": "; ".join(why) if why else "все условия выполнены"})
    best = max((c for c in cands if c["eligible"]), key=lambda c: c["ratio"], default=None)
    others = [c for c in cands if c["key"] != cur]
    doc = {"current": cur, "name": name(cur), "since": st["since"], "by": st["by"], "candidates": cands, "asof": now}
    if best is None:
        if others and all(c["trades"] < MIN_TRADES for c in others):
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
    why = (f"Переключено с «{name(cur)}» на «{best['name']}»: {best['trades']} сделок с 17.10, результат к просадке {_f(best['ratio'])} "
           f"против {_f(best['cur_ratio'])} (лучше на 20% и больше), просадка {ddt}% в пределах истории {best['exp_dd_pct']:g}%, "
           f"{why_real}.")
    return {**doc, "current": best["key"], "name": best["name"], "since": now, "by": "auto", "decision": "switch", "from": cur,
            "why_ru": why, "short_ru": f"переключил с «{name(cur)}» {_ddmm(now)}"}


def run(out: str = "web", now: int | None = None, log_path: str = LOG) -> dict:
    """one engine step: read the desks, the last decision and the real account → web/desk_select.json (+ a log line on a switch)"""
    now = int(now or time.time() * 1000)
    paper = _load(out, "paper.json") or {}
    prof = paper.get("profiles") or {}
    if not prof:
        raise RuntimeError("web/paper.json: нет столов — выбор не пересчитан")
    conv = _load(out, "conveyor.json") or {}
    stages = {d.get("key"): d for d in conv.get("desks") or [] if isinstance(d, dict) and d.get("key")}
    doc = decide(prof, _load(out, FEED), _load(out, "public_real.json"), stages, now)
    if doc["decision"] == "switch":
        line = {"t": now, "from": doc["from"], "to": doc["current"], "why_ru": doc["why_ru"]}
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

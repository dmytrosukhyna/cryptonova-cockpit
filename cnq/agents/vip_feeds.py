"""CRYPTO NOVA VIP feeds (28.10.2026, owner's VIP spec §6, §7.4–7.7, §9, §10, §11, §16) — four read-only files for the closed
panel, built ONLY from what the system already recorded (web/*.json, the spot ledger in data/agents.sqlite, the OKX lease file,
the researcher's log). Nothing here trades, changes a rule, a weight, a filter or a desk; a number that can not be computed
correctly is null, a reason that was never recorded is «причина не записана».

  web/spot_pipeline.json   every coin the spot table evaluated, at ONE stage of the way to the CRYPTO NOVA portfolio:
                           found → first_check → all_filters → recommended → bought, or waiting / rejected on the way;
                           per coin {sym, stage, stage_ru, passed[], why_ru, since, okx_pair, in_real_account, last_action};
                           plus the cash: {cash_pct_real, cash_pct_portfolio, cash_target_pct, cash_target_kind, cash_why_ru, …}.
  web/strategies.json      every desk, portfolio and test with ONE status set (Active / Approved / Validation / Testing /
                           In Development / Paused / Rejected / Archived) and the demo record of each (never mixed with the real
                           account, which is a separate block); the main-desk selector's state and its review.
  web/opportunities.json   the other ways of earning (triangle / cross-exchange arbitrage, funding, fixed-rate carry, stablecoin
                           yield, Polymarket, listing shorts, OKX copy trading / signal): test results and real results apart.
  web/system_status.json   every background part with {key, name_ru, status ok|stale|error|stopped, last_ok, age_min, note_ru}
                           from real timestamps only (age_min is at «asof»; a reader recomputes it from last_ok).

Where it runs: the GitHub pipeline (site.yml), right after the portfolio / ideas / capital steps, so the four files describe the
same run as web/portfolio.json and web/ideas.json.   python -m cnq.cli vipfeeds --out web
Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.request

DAY = 86_400_000
H = 3_600_000
REPO = "dmytrosukhyna/cryptonova-cockpit"
LEASE_URL = f"https://raw.githubusercontent.com/{REPO}/okx-lease/okx_lease.json"
API_HEALTH_URL = "https://app.crypto-capital.app/v2/health"
PUBLIC_D0 = int(dt.datetime(2026, 10, 22, 0, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)   # the public portfolio's day
UNKNOWN_RU = "причина не записана"
FILES = ("spot_pipeline.json", "strategies.json", "opportunities.json", "system_status.json")

try:
    from zoneinfo import ZoneInfo
    KYIV, ATHENS = ZoneInfo("Europe/Kyiv"), ZoneInfo("Europe/Athens")
except Exception:  # noqa: BLE001
    KYIV = ATHENS = dt.timezone(dt.timedelta(hours=2))


# ── small helpers ────────────────────────────────────────────────────────────────────────────────────────────────────
def _load(out: str, name: str):
    try:
        with open(os.path.join(out, name), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _num(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _r(x, n: int = 2):
    v = _num(x)
    return None if v is None else round(v, n)


def _ms(x) -> int | None:
    v = _num(x)
    if not v or v <= 0:
        return None
    return int(v * 1000) if v < 1e12 else int(v)        # seconds or ms


def _ddmm(ms) -> str:
    t = _ms(ms)
    return dt.datetime.fromtimestamp(t / 1000, KYIV).strftime("%d.%m") if t else "—"


def _pct_ru(x, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",").replace("-", "−") + "%"


def _usd_ru(x) -> str:
    v = _num(x, 0.0)
    return ("−" if v < 0 else "") + "$" + f"{abs(v):,.0f}".replace(",", " ")


def _age_min(t, now: int):
    t = _ms(t)
    return None if not t else round((now - t) / 60_000, 1)


def _max_dd_pct(vals: list) -> float | None:
    """the largest peak-to-trough fall of a value series, % (None when fewer than 2 points)"""
    vals = [v for v in (_num(x) for x in vals) if v is not None and v > 0]
    if len(vals) < 2:
        return None
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak)
    return round(dd * 100, 2)


def _write(out: str, name: str, doc: dict) -> None:
    tmp = os.path.join(out, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(out, name))


def _get_json(url: str, timeout: int = 10):
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + f"t={int(time.time())}",
                                 headers={"User-Agent": "cnq-vip-feeds", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "null")


# ═════════════════════════════════════════════════ 1. spot pipeline ═════════════════════════════════════════════════
STAGES = ("found", "first_check", "all_filters", "recommended", "waiting", "rejected", "bought")
STAGE_RU = {"found": "обнаружена", "first_check": "прошла первичную оценку", "all_filters": "прошла все фильтры таблицы",
            "recommended": "рекомендована к покупке", "waiting": "ожидает", "rejected": "отклонена", "bought": "куплена"}
STAGE_ABOUT_RU = {
    "found": "Монета в списке, который проверяет таблица «Что брать на спот», но оценки ещё нет (нет анкеты или проверки мем-деска).",
    "first_check": "Оценка есть, но не все обязательные фильтры таблицы пройдены — таблица пишет «ждём» и почему.",
    "all_filters": "Таблица дала «можно брать / добрать / на небольшую сумму», но портфель её ещё не рассматривал (например, таблица устарела).",
    "recommended": "Прошла фильтры таблицы и правила портфеля: портфель покупает её на ближайшем прогоне.",
    "waiting": "Прошла фильтры таблицы, но правило портфеля временно не даёт купить: 14 дней после продажи, заняты места, кэш у минимума, цена не сверилась.",
    "rejected": "Таблица поставила «не брать», или монету нельзя купить на реальном счёте (нет спот-пары на OKX — правило владельца с 22.10).",
    "bought": "Монета в портфеле CRYPTO NOVA (реальный счёт повторяет его на OKX).",
}
_FOUND = ("Анкеты пока нет", "без проверки мем-деска", "нет данных")
_OKX_NONE = "на споте OKX нет пары"
_ORDER = {"bought": 0, "recommended": 1, "waiting": 2, "all_filters": 3, "first_check": 4, "found": 5, "rejected": 6}


def _ideas_rows(ideas: dict) -> dict:
    """{SYM: row} — every coin the table evaluated (statuses), the shown rows win (same as spot_profiles.ideas_statuses)"""
    out = {str(s).upper(): v for s, v in ((ideas or {}).get("statuses") or {}).items() if isinstance(v, dict)}
    for r in (ideas or {}).get("rows") or []:
        if isinstance(r, dict) and r.get("symbol"):
            out[str(r["symbol"]).upper()] = r
    return out


def ledger_actions(conn) -> list[dict]:
    """the whole action ledger of «CRYPTO NOVA портфель» (spot_ideas_actions) — [] without the database"""
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT time, symbol, action, reason FROM spot_ideas_actions ORDER BY time").fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"time": int(t), "symbol": str(s or "").upper(), "action": a, "reason": r} for t, s, a, r in rows]


def okx_set(conn, now: int) -> set | None:
    """coins with an OKX spot pair (the same daily list the portfolio filters by) — None when unknown"""
    if conn is None:
        return None
    try:
        from .portfolio import okx_spot_list
        return okx_spot_list(conn, now)
    except Exception:  # noqa: BLE001
        return None


def _real_spot(real: dict) -> dict:
    """{COIN: usd} on the real account's spot (web/public_real.json spot rows)"""
    out = {}
    for x in (real or {}).get("spot") or []:
        if isinstance(x, dict) and x.get("sym") and (_num(x.get("usd"), 0) or 0) >= 1:
            out[str(x["sym"]).upper()] = round(_num(x.get("usd"), 0.0), 2)
    return out


def _cross_stop(row: dict) -> bool:
    try:
        from .spot_profiles import cross_stop
        return cross_stop(row)
    except Exception:  # noqa: BLE001
        return False


def _okx_base(sym: str) -> str:
    try:
        from ..execution.okx_alloc import okx_base
        return okx_base(sym)[0]
    except Exception:  # noqa: BLE001
        return sym


def _last_action(sym: str, acts: list, real_acts: list) -> dict | None:
    best = None
    for a in acts:
        if a.get("symbol") == sym and a.get("action") in ("BUY", "ADD", "SELL", "TRIM"):
            if best is None or a["time"] >= best["t"]:
                best = {"t": a["time"], "src": "portfolio", "action": a["action"], "text_ru": a.get("reason") or UNKNOWN_RU}
    base = _okx_base(sym)
    for a in real_acts:
        if str(a.get("sym") or "").upper() == base and str(a.get("kind") or "").startswith("spot_"):
            t = _ms(a.get("t")) or 0
            if best is None or t > best["t"]:
                side = "покупка" if a["kind"] == "spot_buy" else "продажа"
                txt = f"Реальный счёт OKX: {side}" + (f" на {_usd_ru(a.get('usd'))}" if a.get("usd") is not None else "") + \
                      (f" по {_num(a.get('px')):.6g}" if _num(a.get("px")) else "")
                best = {"t": t, "src": "real", "action": "BUY" if a["kind"] == "spot_buy" else "SELL", "text_ru": txt}
    return best


def spot_pipeline(pf: dict, ideas: dict, memes: dict, real: dict, prev: dict | None, okx: set | None, ledger: list,
                  now: int) -> dict:
    ip = ((pf or {}).get("portfolios") or {}).get("ideas") or {}
    rows = _ideas_rows(ideas)
    held = {str(p.get("symbol") or "").upper(): p for p in ip.get("positions") or []
            if isinstance(p, dict) and str(p.get("status") or "OPEN").upper() == "OPEN" and (_num(p.get("weight"), 0) or 0) > 0}
    nb = {str(x.get("symbol") or "").upper(): x for x in ip.get("not_bought") or [] if isinstance(x, dict)}
    seen, acts = set(), []
    for a in list(ledger or []) + [{"time": int(a.get("time") or 0), "symbol": str(a.get("symbol") or "").upper(), "action": a.get("action"),
                                    "reason": a.get("reason")} for a in (ip.get("recent_actions") or []) if isinstance(a, dict)]:
        k = (a["time"], a["symbol"], a.get("action"))
        if k not in seen:
            seen.add(k)
            acts.append(a)
    real_spot = _real_spot(real)
    real_acts = [a for a in (real or {}).get("actions") or [] if isinstance(a, dict)]
    meme_listed = {str(m.get("symbol") or "").upper(): (m.get("listed") or {}).get("okx_spot")
                   for m in (memes or {}).get("spot_memes") or [] if isinstance(m, dict)}
    fresh = bool((ip.get("ideas") or {}).get("fresh"))
    stale_note = (ip.get("ideas") or {}).get("note_ru")
    prev_c = {c.get("sym"): c for c in (prev or {}).get("coins") or [] if isinstance(c, dict)}
    syms = set(rows) | set(held) | set(nb) | {s for s in real_spot if s not in ("USDT", "USDC")}
    coins = []
    for s in sorted(syms):
        r, p, x = rows.get(s) or {}, held.get(s), nb.get(s)
        st, why_t = r.get("status"), (r.get("why_ru") or "").strip()
        since = None
        if p:
            stage, passed = "bought", ["found", "first_check", "all_filters", "recommended", "bought"]
            reco = p.get("reco") or {}
            why = "; ".join(w for w in [f"в портфеле, {_pct_ru(_num(p.get('weight'), 0) * 100)} счёта",
                                        (reco.get("t") + (" — " + reco["why"] if reco.get("why") else "")) if reco.get("t") else ""] if w)
            since = _ms(p.get("entry_time"))
        elif x:
            w = (x.get("why_ru") or "").strip() or UNKNOWN_RU
            if _OKX_NONE in w:
                stage, passed = "rejected", ["found", "first_check", "all_filters"]
            elif w.startswith("цена") or "живой цены" in w or "в таблице нет цены" in w:
                stage, passed = "waiting", ["found", "first_check", "all_filters", "recommended"]
            else:
                stage, passed = "waiting", ["found", "first_check", "all_filters"]
            why = f"Таблица: «{r.get('status_ru') or st or '—'}»" + (f" ({why_t})" if why_t else "") + f". Портфель не покупает: {w}."
            if "продана" in w:
                since = max((a["time"] for a in acts if a["symbol"] == s and a.get("action") == "SELL"), default=None)
        elif st in ("buy", "add", "small"):
            stage, passed = "all_filters", ["found", "first_check", "all_filters"]
            if fresh:
                stage, passed = "recommended", passed + ["recommended"]
                why = f"Таблица: «{r.get('status_ru') or st}» ({why_t or UNKNOWN_RU}); портфель покупает на ближайшем прогоне."
            else:
                why = f"Таблица: «{r.get('status_ru') or st}» ({why_t or UNKNOWN_RU}). Портфель не покупает: {stale_note or UNKNOWN_RU}"
        elif st == "avoid" and _cross_stop(r):
            stage, passed = "first_check", ["found", "first_check"]
            why = (f"Таблица: «не брать» — {why_t or UNKNOWN_RU}. Для портфеля CRYPTO NOVA это «не добирать»: не покупает, но и не "
                   "продаёт (решение владельца 28.10).")
        elif st == "avoid":
            stage, passed = "rejected", ["found", "first_check"]
            why = f"Таблица: «не брать» — {why_t or UNKNOWN_RU}."
        elif st == "wait":
            if any(k in why_t for k in _FOUND):
                stage, passed = "found", ["found"]
            else:
                stage, passed = "first_check", ["found", "first_check"]
            why = f"Таблица: «ждём» — {why_t or UNKNOWN_RU}."
        else:
            stage, passed = "found", ["found"]
            why = UNKNOWN_RU if not r else f"Таблица: {r.get('status_ru') or st or '—'} — {why_t or UNKNOWN_RU}."
        base = _okx_base(s)
        in_real = base in real_spot
        if in_real and not p:
            why += f" На реальном счёте есть {base} ({_usd_ru(real_spot[base])}), а в портфеле CRYPTO NOVA её нет — автопилот её продаёт."
        if okx is not None:
            okx_pair = base in okx
        elif s in meme_listed and meme_listed[s] is not None:
            okx_pair = bool(meme_listed[s])
        elif x and _OKX_NONE in (x.get("why_ru") or ""):
            okx_pair = False
        elif p:
            okx_pair = True
        else:
            okx_pair = None
        pc = prev_c.get(s)
        if since is None:
            since = int(pc["since"]) if pc and pc.get("stage") == stage and pc.get("since") else now
        coins.append({"sym": s, "name": r.get("name") or (p or {}).get("name") or s, "kind": r.get("kind") or (p or {}).get("bucket"),
                      "table_status": st, "stage": stage, "stage_ru": STAGE_RU[stage], "passed": passed, "why_ru": why,
                      "since": since, "okx_pair": okx_pair, "in_real_account": in_real,
                      "last_action": _last_action(s, acts, real_acts)})
    coins.sort(key=lambda c: (_ORDER[c["stage"]], c["sym"]))
    counts = {k: sum(1 for c in coins if c["stage"] == k) for k in STAGES}
    reached = {k: sum(1 for c in coins if k in c["passed"]) for k in ("found", "first_check", "all_filters", "recommended", "bought")}
    return {"asof": now, "portfolio_asof": pf.get("asof"), "ideas_asof": (ideas or {}).get("asof"), "table_fresh": fresh,
            "stages": [{"key": k, "name_ru": STAGE_RU[k], "about_ru": STAGE_ABOUT_RU[k], "now": counts[k],
                        "reached": reached.get(k)} for k in STAGES],
            "counts": counts, **cash_block(ip, real, nb, acts, now), "coins": coins,
            "about_ru": ("Путь монеты от таблицы «Что брать на спот» до портфеля CRYPTO NOVA и реального счёта OKX. Причины — только "
                         "записанные системой; «причина не записана» — если её нет в данных. «since» — время сделки или первый раз, "
                         "когда этот файл увидел монету на этой стадии.")}


def cash_block(ip: dict, real: dict, nb: dict, acts: list, now: int) -> dict:
    """the cash of the real account's spot part vs the portfolio's rule — numbers and reasons from the data only"""
    try:
        from .spot_profiles import IDEAS
        floor, buy_n, small_n = float(IDEAS["min_cash"]), int(IDEAS["buy_n"]), int(IDEAS["small_n"])
    except Exception:  # noqa: BLE001
        floor, buy_n, small_n = 0.10, 8, 3
    al = (real or {}).get("alloc") or {}
    sc, sk = _num(al.get("spot_cash_usd")), _num(al.get("spot_coins_usd"))
    cash_real = round(sc / (sc + sk) * 100, 1) if sc is not None and sk is not None and sc + sk > 0 else None
    cash_pf = _num(((ip.get("sleeves") or {}).get("cash") or {}).get("now_pct"), _num(ip.get("cash_pct")))
    pos = [p for p in ip.get("positions") or [] if isinstance(p, dict)]
    n_small = sum(1 for p in pos if p.get("role") == "small")
    n_buy = len(pos) - n_small
    free_buy, free_small = max(0, buy_n - n_buy), max(0, small_n - n_small)
    parts = []
    if cash_real is not None:
        parts.append(f"Кэш спот-части реального счёта — {_pct_ru(cash_real)} ({_usd_ru(sc)} из {_usd_ru(sc + sk)}).")
    if cash_pf is not None:
        parts.append(f"В портфеле CRYPTO NOVA, который счёт повторяет, — {_pct_ru(cash_pf)}.")
    idle = ip.get("idle_cash") or {}
    parts.append(f"{floor * 100:.0f}% — это нижняя граница кэша, а не цель покупок: портфель тратит кэш на монеты из таблицы, "
                 "которые проходят все его правила.")
    if idle.get("rule_ru"):
        parts.append("С 28.10 (решение владельца): если кэш выше 10% три полных дня подряд и подходящих монет нет, кэш сверх 10% "
                     "уходит в ядро BTC / ETH / SOL (30 / 10 / 5); новую подходящую монету портфель оплатит сначала этой доливкой.")
        if idle.get("due"):
            parts.append(f"Сейчас кэш «лишний» с {dt.datetime.fromtimestamp(idle['since'] / 1000, KYIV).strftime('%d.%m %H:%M')}; "
                         f"если подходящих монет не появится, перевод в ядро — {dt.datetime.fromtimestamp(idle['due'] / 1000, KYIV).strftime('%d.%m %H:%M')} по Киеву.")
        if idle.get("core_extra_total_pct"):
            parts.append(f"В ядре сейчас доливки «кэш → ядро»: {_pct_ru(idle['core_extra_total_pct'])} счёта.")
    parts.append(f"Свободных мест: {free_buy} из {buy_n} для «можно брать» (по 7,5%) и {free_small} из {small_n} «на небольшую сумму» (по 2,5%).")
    # sales of the last 14 days whose money stayed in cash (the 22.10 «no OKX pair» sales went into the core the same run — not these)
    sells = [a for a in sorted(acts, key=lambda a: -a["time"]) if a.get("action") == "SELL" and now - a["time"] <= 14 * DAY
             and a["time"] >= PUBLIC_D0 and "спот-парой на OKX" not in (a.get("reason") or "")][:4]
    if sells:
        parts.append("Кэш вырос после продаж: " + "; ".join(
            f"{a['symbol']} {_ddmm(a['time'])} — «{(a.get('reason') or UNKNOWN_RU).rstrip('.')}»" for a in sells) + ".")
    if nb:
        parts.append("Кандидаты таблицы сейчас не проходят правила портфеля: " + "; ".join(
            f"{s} — {(x.get('why_ru') or UNKNOWN_RU).rstrip('.')}" for s, x in nb.items()) + ".")
    elif free_buy or free_small:
        parts.append("Новых кандидатов «можно брать» в таблице сейчас нет.")
    acc = {}
    for k, name in (("spot_cash_usd", "спот: кэш портфеля"), ("active_wait_usd", "активный спот «Импульс BTC» ждёт сигнала"),
                    ("fut_reserve_usd", "фьючерсы: резерв без открытых сделок"), ("fut_margin_usd", "фьючерсы: маржа в сделках")):
        if _num(al.get(k)) is not None:
            acc[k] = {"usd": round(_num(al.get(k)), 2), "name_ru": name}
    return {"cash_pct_real": cash_real, "cash_pct_portfolio": cash_pf, "cash_target_pct": round(floor * 100, 1), "idle_cash": idle or None,
            "cash_target_kind": "minimum", "cash_free_slots": {"buy": free_buy, "buy_total": buy_n, "small": free_small, "small_total": small_n},
            "account_usdt_pct": _r((real or {}).get("usdt_pct"), 2), "account_usdt_parts": acc,
            "real_asof": (real or {}).get("asof"), "real_stale": bool((real or {}).get("stale")),
            "cash_why_ru": " ".join(parts)}


# ═════════════════════════════════════════════════ 2. strategies ═════════════════════════════════════════════════════
STATUS_RU = {"Active": "Используется", "Approved": "Допущена", "Validation": "Дополнительная проверка", "Testing": "Тестирование",
             "In Development": "Разработка", "Paused": "Приостановлена", "Rejected": "Отклонена", "Archived": "Архив"}
CATEGORY = {"Active": "used", "Approved": "validation", "Validation": "validation", "Testing": "testing", "In Development": "testing",
            "Paused": "paused", "Rejected": "rejected", "Archived": "rejected"}
CATEGORY_RU = {"used": "Реально используемые", "validation": "Ожидают дополнительной проверки", "testing": "Проходят тестирование",
               "paused": "Приостановленные", "rejected": "Отклонённые и архивные"}
_S_ORDER = {k: i for i, k in enumerate(STATUS_RU)}
_C_ORDER = {k: i for i, k in enumerate(("used", "validation", "testing", "paused", "rejected"))}
_T_ORDER = {"futures": 0, "spot": 1, "other": 2}
STATUS_RULES_RU = [
    "Active — по ней сейчас торгует реальный счёт CRYPTO NOVA на OKX (основной стол выбирает web/desk_select.json, «Импульс BTC» — "
    "вторая половина фьючерсов, «Активный спот · Импульс BTC» — 10% счёта, «CRYPTO NOVA портфель» — спот 65% и доп. ядро BTC/ETH/SOL 10%).",
    "Approved — прошла ворота конвейера PAPER → READY (сейчас таких нет). На реальные деньги — только решением владельца или автовыбора.",
    "Validation — кандидат на реальные деньги, ждёт своих ворот: автовыбор основного стола (20 сделок, результат к просадке лучше на 20%) "
    "или ворота «Сигналов CRYPTO NOVA» (30 сделок, от +0,3% на сделку).",
    "Testing — работает на демо-счёте или в наблюдении, к реальным деньгам не допущена.",
    "In Development — идея из лаборатории или исследования, ещё не запущена даже на демо.",
    "Paused — снята конвейером (паузу снимает только владелец).",
    "Rejected / Archived — отклонена проверкой или остановлена; история сохраняется.",
]


def _st(key, name, typ, status, why, **kw) -> dict:
    d = {"key": key, "name_ru": name, "type": typ, "status": status, "status_ru": STATUS_RU[status], "status_why_ru": why,
         "real_money": status == "Active", "category": CATEGORY[status], "since": None, "trades": None, "win_rate": None,
         "net_usd": None, "net_pct": None, "max_dd_pct": None, "avg_trade_pct": None, "updated": None, "next_step_ru": None,
         "issues_ru": [], "sample_note_ru": None, "metrics_basis_ru": None, "about_ru": None, "source": None}
    d.update({k: v for k, v in kw.items() if k in d or k in ("real", "backtest_ru", "group")})
    return d


def _sample_note(n) -> str | None:
    if n is None:
        return None
    if n == 0:
        return "Закрытых сделок ещё нет — оценивать рано."
    if n < 30:
        return f"{n} закрытых {'сделка' if n % 10 == 1 and n % 100 != 11 else 'сделки' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'сделок'} — маленькая выборка: процент выигрышей ещё ничего не доказывает (конвейеру нужно 30, автовыбору — 20)."
    return f"{n} закрытых сделок."


VALUE_TABLE = {"ideas": "spot_ideas_value", "cons": "spot_cons_value", "high": "spot_high_value", "bal": "spot_bal_value", "mod": "spot_value"}


def _db_dd(conn, key: str, since: int | None) -> float | None:
    """max drawdown % of a spot portfolio from its FULL value ledger (the published history is thinned — not exact)"""
    t = VALUE_TABLE.get(key)
    if conn is None or not t or not since:
        return None
    try:
        rows = conn.execute(f"SELECT index_value FROM {t} WHERE time >= ? ORDER BY time", (int(since),)).fetchall()
    except Exception:  # noqa: BLE001
        return None
    return _max_dd_pct([r[0] for r in rows])


def _fut_eq(v: dict) -> float:
    try:
        from .panel_header import fut_eq
        return fut_eq(v)
    except Exception:  # noqa: BLE001
        return _num(v.get("equity"), 10_000.0) + _num(v.get("open_pnl_usd"), 0.0)


def strategies(paper: dict, conv: dict, sel: dict, pf: dict, app_sig: dict, whales: dict, memes: dict, capital: dict, alt: dict,
               ivan_blob: dict, ivan_stats: dict | None, real: dict, now: int, conn=None) -> dict:
    prof = (paper or {}).get("profiles") or {}
    cdesks = {d.get("key"): d for d in (conv or {}).get("desks") or [] if isinstance(d, dict) and d.get("key")}
    try:
        from .panel_header import NOVA_BASE, main_desk
        main, second = main_desk(sel), NOVA_BASE[1]
    except Exception:  # noqa: BLE001
        main, second = (sel or {}).get("current") or "aggr", "imp"
    try:
        from ..execution.okx_alloc import SHARES
    except Exception:  # noqa: BLE001
        SHARES = {"spot": 0.65, "core": 0.10, "futures": 0.15, "active": 0.10}      # owner's decision of 28.10
    sel_c = {c.get("key"): c for c in (sel or {}).get("candidates") or [] if isinstance(c, dict)}
    out = []
    # ---- the paper desks (web/paper.json) with the conveyor's record (web/conveyor.json) ----
    for k, v in prof.items():
        if not isinstance(v, dict):
            continue
        c = cdesks.get(k) or {}
        spot = k == "imps"
        if k == main:
            status, why = "Active", f"основной стол реальных фьючерсов (автовыбор: «{(sel or {}).get('short_ru') or '—'}», с {_ddmm((sel or {}).get('since'))})"
        elif k == second:
            status, why = "Active", "вторая половина фьючерсов реального счёта — всегда"
        elif k == "imps":
            status, why = "Active", f"активный спот реального счёта ({SHARES.get('active', 0.1) * 100:.0f}% счёта; между сделками — в BTC, после выхода до 3 дней в USDT)"
        elif c.get("paused") or c.get("stage") == "retired":
            status, why = "Paused", c.get("reason_ru") or "снят конвейером"
        elif c.get("stage") == "ready":
            status, why = "Approved", "прошёл ворота конвейера PAPER → READY"
        elif k in ("cons", "bal", "aggr"):
            status, why = "Validation", ("размер семейства «Автопилот», которое сейчас ведёт реальные фьючерсы размером «"
                                         + ((prof.get(main) or {}).get("name") or main) + "»; автовыбор размеры не переключает (решение владельца 28.10)")
        elif k in sel_c or k == "own":
            status, why = "Validation", "кандидат автовыбора основного стола: нужна готовность READY по конвейеру, 20+ сделок и лучший результат к просадке"
        else:
            status, why = "Testing", "демо-счёт $10 000, к реальным деньгам не допущен"
        start = _num(v.get("start_equity"), 10_000.0) or 10_000.0
        net = _fut_eq(v) - start
        n = c.get("trades") if c.get("trades") is not None else (v.get("stats") or {}).get("trades")
        n = int(n) if n is not None else None
        issues = []
        if c.get("caveat_ru"):
            issues.append(c["caveat_ru"])
        if c.get("evidence") and c.get("evidence") != "oos" and c.get("evidence_ru"):
            issues.append("Проверка на истории: " + c["evidence_ru"] + ".")
        if status == "Active" and not n:
            issues.append(f"С {_ddmm(c.get('since') or v.get('since'))} ни одной закрытой сделки — эта часть реального счёта ждёт сигнала.")
        if k in sel_c and sel_c[k].get("why") and k != main:
            issues.append("Автовыбор: " + sel_c[k]["why"] + ".")
        gate = c.get("reason_ru")
        nxt = (f"Ворота конвейера PAPER → READY: от 30 сделок и 21 дня, средняя сделка от +0,3%, профит-фактор от 1,2, просадка в пределах. "
               f"Сейчас: {gate}.") if gate else None
        out.append(_st(k, v.get("name") or k, "spot" if spot else "futures", status, why,
                       since=_ms(c.get("since") or v.get("since")), trades=n,
                       win_rate=_r(c.get("winrate_pct"), 1) if n else None, net_usd=round(net, 2), net_pct=round(net / start * 100, 2),
                       max_dd_pct=_r(c.get("max_dd_pct") if c.get("max_dd_pct") is not None else (v.get("stats") or {}).get("max_dd_pct"), 2),
                       avg_trade_pct=_r(c.get("mean_pct"), 2) if n else None, updated=_ms(v.get("asof") or (paper or {}).get("asof")),
                       next_step_ru=nxt, issues_ru=issues, sample_note_ru=_sample_note(n), about_ru=v.get("about"), source="paper.json + conveyor.json",
                       metrics_basis_ru="Демо-счёт $10 000 по ценам биржи: комиссии, проскальзывание и фандинг учтены; результат — с открытыми позициями по последней цене; сделки, выигрыши и средняя сделка — по журналу конвейера.",
                       real=({"sleeve": "spot_active" if spot else "futures",
                              "share_pct": round((SHARES.get("active", 0.1) if spot else SHARES.get("futures", 0.15) / 2) * 100, 1)}
                             if status == "Active" else None)))
    # ---- spot portfolios (web/portfolio.json) — the meme one is the meme desk below ----
    for k, p in ((pf or {}).get("portfolios") or {}).items():
        if not isinstance(p, dict) or k == "meme":
            continue
        idx = _num(p.get("index"))
        st0 = _num(p.get("start_usd"), 10_000.0) or 10_000.0
        net = (idx / 100 - 1) * st0 if idx else None
        ha = p.get("history_all") or []
        since = _ms(p.get("started")) or _ms((p.get("reset") or {}).get("time")) or (_ms(ha[0].get("time")) if ha else None)
        dd = _db_dd(conn, k, since)
        if k == "ideas":
            status, why = "Active", f"спот реального счёта повторяет его ({SHARES.get('spot', 0.65) * 100:.0f}% счёта)"
        else:
            status, why = "Testing", "модельный спот-портфель на $10 000, к реальным деньгам не допущен"
        issues = [f"Ошибка прогона: {p['error']}"] if p.get("error") else []
        if (p.get("reset") or {}).get("note_ru"):
            issues.append(p["reset"]["note_ru"])
        out.append(_st(f"spot_{k}", p.get("name") or k, "spot", status, why, since=since, net_usd=_r(net, 2),
                       net_pct=_r(net / st0 * 100, 2) if net is not None else None, max_dd_pct=dd, updated=_ms((pf or {}).get("asof")),
                       issues_ru=issues, about_ru=p.get("about"), source="portfolio.json",
                       sample_note_ru="Спот-портфель держит позиции, а не закрывает сделки: процент выигрышей не считаем.",
                       metrics_basis_ru="Модельный счёт $10 000: цены CoinGecko / Hyperliquid, комиссия 0,1% с каждой сделки; результат — по индексу счёта."
                                        + (" Максимальная просадка — по полной истории индекса в базе." if dd is not None else " Полной истории индекса здесь нет — просадку не показываем."),
                       next_step_ru=None, real=({"sleeve": "spot", "share_pct": round(SHARES.get("spot", 0.65) * 100, 1)} if k == "ideas" else None)))
    # ---- the meme desk (memes.json account) ----
    A = (memes or {}).get("account") or {}
    if A:
        n = (A.get("stats") or {}).get("trades")
        wins = (A.get("stats") or {}).get("wins")
        st0 = _num(A.get("start_equity"), 10_000.0) or 10_000.0
        out.append(_st("meme_desk", A.get("name") or "Мем-портфель", "futures", "Testing", "эксперимент на демо-счёте",
                       since=_ms(((A.get("watch") or {}).get("since"))), trades=n, win_rate=_r(wins / n * 100, 1) if n else None,
                       net_usd=_r(_num(A.get("equity"), st0) - st0, 2), net_pct=_r((_num(A.get("equity"), st0) / st0 - 1) * 100, 2),
                       updated=_ms((memes or {}).get("asof")), sample_note_ru=_sample_note(n), about_ru=A.get("rules"), source="memes.json",
                       metrics_basis_ru="Демо-счёт $10 000, мем-фьючерсы Hyperliquid; комиссия с проскальзыванием и фандинг учтены."))
    # ---- the CRYPTO NOVA app's signals replayed under exit policies (app_signals.json) ----
    sl = (((app_sig or {}).get("accounts") or {}).get("since_live") or {})
    main_pol = (app_sig or {}).get("main_policy")
    for k, a in sl.items():
        if not isinstance(a, dict) or "trades" not in a:
            continue
        n, st0 = a.get("trades"), _num(a.get("start"), 10_000.0) or 10_000.0
        eq = _num(a.get("equity_with_open"), _num(a.get("equity")))
        out.append(_st(f"app_{k}", "Сигналы приложения · " + (a.get("name_ru") or k), "futures", "Testing",
                       "сигналы приложения проигрываются на демо-счёте" + (" (основная политика)" if a.get("policy") == main_pol else ""),
                       since=_ms(a.get("start_t")), trades=n, win_rate=_r(_num(a.get("winrate")) * 100, 1) if n and a.get("winrate") is not None else None,
                       net_usd=_r(eq - st0, 2) if eq is not None else None, net_pct=_r((eq / st0 - 1) * 100, 2) if eq is not None else None,
                       max_dd_pct=_r(a.get("max_dd_pct"), 2), updated=_ms((app_sig or {}).get("asof")), sample_note_ru=_sample_note(n),
                       about_ru=a.get("rules_ru"), source="app_signals.json", group="app_signals",
                       metrics_basis_ru="Демо-счёт $10 000: сигналы приложения по ценам 15-минутных свечей; результат — с открытыми позициями."))
    # ---- whales copy desk ----
    W = (whales or {}).get("desk") or {}
    if W:
        n = W.get("trades")
        out.append(_st("whales_copy", W.get("name_ru") or "Киты · копирование", "futures", "Testing", "эксперимент на демо-счёте",
                       since=_ms(W.get("started")), trades=n, win_rate=_r(_num(W.get("winrate")) * 100, 1) if n and W.get("winrate") is not None else None,
                       net_usd=_r(W.get("pnl_usd"), 2), net_pct=_r(W.get("pnl_pct"), 2), max_dd_pct=_r(W.get("max_dd_pct"), 2),
                       updated=_ms((whales or {}).get("asof")), sample_note_ru=_sample_note(n), about_ru=W.get("rules_ru"), source="whales_top.json",
                       metrics_basis_ru="Демо-счёт $10 000, комиссии 0,11% за сделку учтены."))
    # ---- «Иван тест» — a third-party platform on a Bybit demo account; only a stats-only summary may be public ----
    iv_asof = _ms((ivan_blob or {}).get("asof"))
    s = ivan_stats or {}
    iv_issues = ["Стратегия чужая: правила не раскрыты, мы только читаем счёт ключом «только чтение»."]
    if iv_asof and now - iv_asof > 6 * H:
        iv_issues.append(f"Монитор не обновлялся с {dt.datetime.fromtimestamp(iv_asof / 1000, KYIV).strftime('%d.%m %H:%M')} "
                         "(он работал в зеркале на Маке; на сервере его нет).")
    if not s:
        iv_issues.append("Статистика лежит только в зашифрованном web/ivan.enc.json: без пароля IVAN_PASS её не пересчитать.")
    n = s.get("closed_n")
    out.append(_st("ivan", "Иван тест", "futures", "Testing", "чужая стратегия на демо-счёте Bybit — только наблюдение",
                   since=_ms(s.get("since")), trades=n, win_rate=_r(s.get("win_rate"), 1) if n else None,
                   net_usd=_r(s.get("net_closed_usd"), 2), updated=_ms(s.get("asof")) or iv_asof, issues_ru=iv_issues,
                   sample_note_ru=(f"{_pct_ru(s.get('win_rate'), 0)} · {n} закрытых сделок; открыто {s.get('open_n', 0)}. "
                                   "Процент выигрышей считает только закрытые сделки: убыток в открытых позициях в него не входит."
                                   if n else None),
                   next_step_ru="Перенести монитор на сервер (ключ «только чтение» в /etc/cnq-okx.env), чтобы статистика обновлялась без Мака.",
                   source="ivan_stats.json" if s else "ivan.enc.json (только время)",
                   metrics_basis_ru="Закрытые сделки Bybit (closed-pnl, после комиссий); безубыточная сделка в знаменателе, но не выигрыш."))
    out.append(_st("roman", "Роман тест", "futures", "In Development", "ещё не подключена: нет ни ключа, ни данных",
                   next_step_ru="Подключить так же, как Ивана: ключ биржи «только чтение».", source="—"))
    # ---- allocator / models ----
    if capital:
        st0 = _num(capital.get("start_usd"), 10_000.0) or 10_000.0
        out.append(_st("capital", capital.get("name") or "CRYPTO NOVA Портфолио", "other", "Testing",
                       "демо-распределитель $10 000; публичный портфель — реальный счёт с 22.10, не эта модель",
                       since=_ms(capital.get("started")), net_usd=_r(capital.get("pnl_usd"), 2), net_pct=_r(capital.get("pnl_pct"), 2),
                       updated=_ms(capital.get("asof")), about_ru=capital.get("why_ru"), source="capital.json",
                       metrics_basis_ru="Демо-счёт $10 000, распределённый между направлениями; в файле только последние точки истории — просадку не показываем."))
    if alt:
        out.append(_st("altseason", alt.get("name") or "ядро + альтсезон", "spot", "Testing", "модель долей, без счёта",
                       updated=_ms(alt.get("asof")), about_ru=alt.get("rules_ru"), source="altseason.json",
                       metrics_basis_ru="Модель долей без собственного счёта: результата нет."))
    # ---- the lab: research rules that run as no desk ----
    for x in (conv or {}).get("lab") or []:
        if not isinstance(x, dict) or not x.get("id"):
            continue
        name = x.get("name_ru") or x["id"]
        if x.get("refuted_by"):
            status, why = "Rejected", f"опровергнуто: {x['refuted_by']}"
        elif x.get("recommend_paper"):
            status, why = "Validation", "лаборатория рекомендует запустить на демо (решает владелец)"
        else:
            status, why = "In Development", x.get("reason_ru") or "не прошло отбор лаборатории"
        typ = "other" if re.search(r"\bfx\b|FX|валют|металл|золот", x["id"] + " " + name, re.I) else "futures"
        out.append(_st(f"lab_{x['id']}", name, typ, status, why, source=x.get("doc") or "autolab.json", group="lab",
                       backtest_ru=x.get("metric_ru"), next_step_ru=x.get("running_ru"),
                       sample_note_ru="Только проверка на истории — живых сделок нет."))
    out.sort(key=lambda d: (_C_ORDER[d["category"]], _S_ORDER[d["status"]], 1 if d.get("group") == "lab" else 0,
                            _T_ORDER[d["type"]], -(d["updated"] or 0) if d.get("group") != "lab" else 0, d["name_ru"], d["key"]))
    al = (real or {}).get("alloc") or {}
    real_block = {"source": "public_real.json", "since": _ms((real or {}).get("since")), "start_usd": _r((real or {}).get("start_usd")),
                  "equity_usd": _r((real or {}).get("equity_usd")), "pnl_usd": _r((real or {}).get("pnl_usd")),
                  "spot_pnl_usd": _r((real or {}).get("spot_pnl_usd")), "fut_pnl_usd": _r((real or {}).get("fut_pnl_usd")),
                  "fut_closed_n": (real or {}).get("closed_n"), "asof": _ms((real or {}).get("asof")), "stale": bool((real or {}).get("stale")),
                  "split_pct": {k: _num(al.get(f"{k}_pct")) for k in ("spot", "fut", "active")},
                  "note_ru": "Реальный счёт CRYPTO NOVA на OKX с 22.10.2026 — отдельно от демо-результатов стратегий ниже."} if real else None
    return {"asof": now, "statuses": [{"key": k, "name_ru": v, "category": CATEGORY[k]} for k, v in STATUS_RU.items()],
            "status_rules_ru": STATUS_RULES_RU, "categories": [{"key": k, "name_ru": v} for k, v in CATEGORY_RU.items()],
            "sort_ru": "Категория → статус → тип (фьючерсы, спот, другое) → недавно обновлённые → название.",
            "real_account": real_block, "selector": selector_block(sel, cdesks), "strategies": out,
            "counts": {c: sum(1 for d in out if d["category"] == c) for c in CATEGORY_RU}}


def selector_block(sel: dict, cdesks: dict) -> dict:
    """the main futures desk selector (web/desk_select.json) and the review against the owner's spec §9.4"""
    review = [
        "Метрика — результат к максимальной просадке за одно и то же окно (с 17.10 или с более позднего старта обоих столов); "
        "процент выигрышей не используется.",
        "Выборка: претенденту нужно 20 закрытых демо-сделок и (с 28.10, решение владельца) готовность READY по конвейеру — 30 сделок, "
        "21 день, профит-фактор от 1,2, средняя сделка от +0,3%; просадка должна быть в пределах того, что обещала история стола.",
        "Комиссии, проскальзывание и фандинг учтены в демо-счетах; проверка вне выборки — только «частичная» у всех столов.",
        "Переключение — не чаще раза в 7 дней и никогда при открытой позиции текущего стола на реальном счёте.",
        "История решений: каждое переключение — строка в data/desk_select.jsonl на сервере, а состояние — в истории web/desk_select.json.",
        "«Консервативная», «Сбалансированная» и «Агрессивная» — одно семейство «Автопилот» (одни сигналы разного размера): с 28.10 "
        "автовыбор сравнивает семейства, а не размеры; размер семейства — «Агрессивная» (выбор владельца).",
    ]
    if not sel:
        return {"available": False, "review_ru": review}
    return {"available": True, "current": sel.get("current"), "name_ru": sel.get("name"), "since": sel.get("since"), "by": sel.get("by"),
            "decision": sel.get("decision"), "why_ru": sel.get("why_ru"), "short_ru": sel.get("short_ru"), "asof": sel.get("asof"),
            "candidates": sel.get("candidates") or [], "review_ru": review,
            "bugs_found_ru": []}


# ═════════════════════════════════════════════════ 3. other opportunities ════════════════════════════════════════════
OPP_STATUS_RU = {"Testing": "Тестирование", "Monitoring": "Наблюдение", "Not Active": "Не используется", "Approved": "Допущена",
                 "Active": "Используется", "Paused": "Приостановлена"}


def _opp(key, name, about, status, **kw) -> dict:
    d = {"key": key, "name_ru": name, "about_ru": about, "status": status, "status_ru": OPP_STATUS_RU[status],
         "result_real_usd": None, "result_test_usd": None, "result_test_note_ru": None, "ops": None, "ops_ru": None,
         "risks_ru": [], "why_not_used_ru": None, "since": None, "updated": None, "source": None}
    d.update(kw)
    return d


def opportunities(tri: dict, arb: dict, funding: dict, basis: dict, yld: dict, paper: dict, real: dict, okx_sig: dict,
                  now: int) -> dict:
    out = []
    if tri:
        w = tri.get("week") or {}
        out.append(_opp("tri", "Треугольный арбитраж", "Три обмена внутри одной биржи (например, USDT → BTC → XRP → USDT): плюс, если "
                        "круг даёт больше трёх комиссий.", "Monitoring", result_test_usd=_r(w.get("virtual_pnl_usd")),
                        result_test_note_ru="Виртуальная оценка за 7 дней по окнам, которые увидел сервер (до $1 000 на окно, не больше самого тонкого стакана). Не сделки.",
                        ops=w.get("windows"), ops_ru="окон за 7 дней (виртуально)",
                        risks_ru=["Настоящие окна на крупных биржах живут миллисекунды и достаются роботам маркет-мейкеров с нулевой комиссией.",
                                  "Застывшие цены: окно, которое держится часами, обычно нельзя забрать (закрыт ввод/вывод, пустой стакан).",
                                  "Комиссия тейкера на каждом из трёх шагов."],
                        why_not_used_ru=tri.get("note") or UNKNOWN_RU, since=_ms(tri.get("since")), updated=_ms(tri.get("asof")), source="tri.json"))
    if arb:
        w = arb.get("week") or {}
        out.append(_opp("arb", "Межбиржевой арбитраж", "Монета на одной бирже дешевле, чем на другой, больше чем на комиссии обеих бирж.",
                        "Monitoring", result_test_usd=_r(w.get("virtual_pnl_usd")),
                        result_test_note_ru=f"Виртуальная оценка за 7 дней (без «застывших» окон; вместе с ними было бы {_usd_ru(w.get('virtual_pnl_naive_usd'))}). Не сделки.",
                        ops=w.get("windows"), ops_ru="окон за 7 дней (виртуально)",
                        risks_ru=["Деньги нужны заранее на обеих биржах; перевод между биржами занимает время и стоит денег.",
                                  "Большая разница почти всегда «застывшая»: на бирже закрыт ввод/вывод монеты или пустой стакан.",
                                  "Оценка оптимистичная: исполнение по лучшей цене без задержки."],
                        why_not_used_ru=arb.get("note") or UNKNOWN_RU, since=_ms(arb.get("since")), updated=_ms(arb.get("asof")), source="arb.json"))
    if funding:
        st0 = _num(funding.get("start_equity"), 10_000.0) or 10_000.0
        v = _num(funding.get("equity_mtm"))
        res = (v - st0) if v is not None else _num(funding.get("pnl_usd"))
        out.append(_opp("funding", funding.get("name") or "Фандинг · без направления", funding.get("about") or "", "Testing",
                        result_test_usd=_r(res), result_test_note_ru="Демо-счёт $10 000 с ценовой ногой (спот против фьючерса); комиссии входа учтены.",
                        ops=len((funding.get("positions") or [])), ops_ru="открытых пар спот + фьючерс сейчас",
                        risks_ru=["Ставка фандинга может упасть или стать отрицательной.", "Комиссия входа и выхода 0,30% круга окупается неделями.",
                                  "История 2025–2026: около +1% в год на крупных монетах."],
                        why_not_used_ru="На реальном счёте нет такого рукава: владелец оставил на OKX только спот, фьючерсы CRYPTO NOVA и активный спот (22.10).",
                        since=_ms((funding.get("history") or [[None]])[0][0]) if funding.get("history") else None,
                        updated=_ms(funding.get("asof")), source="funding.json"))
    if basis:
        out.append(_opp("basis", basis.get("name") or "Фикс-ставка на фьючерсах", basis.get("about_ru") or "", "Testing",
                        result_test_usd=_r(basis.get("pnl_usd")), result_test_note_ru="Демо-счёт $10 000; ставка фиксируется при входе.",
                        ops=len(basis.get("events") or []), ops_ru="входов/выходов",
                        risks_ru=["Риск биржи (Deribit) и досрочного выхода.", "Комиссии."],
                        why_not_used_ru="На реальном счёте нет такого рукава (решение владельца 22.10: только спот, фьючерсы и активный спот).",
                        since=_ms(basis.get("since")), updated=_ms(basis.get("asof")), source="basis.json"))
    if yld:
        out.append(_opp("yield", yld.get("name") or "Доходность стейблкоинов", yld.get("about_ru") or "", "Testing",
                        result_test_usd=_r(yld.get("pnl_usd")), result_test_note_ru=f"Демо: проценты по текущей ставке DefiLlama (сейчас ≈{_num(yld.get('apy_now'), 0):g}% годовых).",
                        risks_ru=["Ошибка в смарт-контракте.", "Отвязка стейблкоина от доллара.", "Ставка плавающая и может упасть."],
                        why_not_used_ru="На реальном счёте нет DeFi-рукава (решение владельца 22.10).",
                        since=_ms(yld.get("since")), updated=_ms(yld.get("asof")), source="yield.json"))
    lst = ((paper or {}).get("profiles") or {}).get("lst") or {}
    if lst:
        n = (lst.get("stats") or {}).get("trades")
        st0 = _num(lst.get("start_equity"), 10_000.0) or 10_000.0
        out.append(_opp("listings_short", lst.get("name") or "Шорт листингов", lst.get("about") or "Шорт новых бессрочных фьючерсов через 4 часа после листинга.",
                        "Testing", result_test_usd=_r(_fut_eq(lst) - st0), result_test_note_ru="Демо-счёт $10 000 (стол «Шорт листингов» в стратегиях).",
                        ops=n, ops_ru="закрытых сделок", risks_ru=["Резкий рост после листинга: стоп выбивает.", "Новых крипто-листингов стало мало (с 06.2026 около 1–2 в месяц)."],
                        why_not_used_ru="Демо-стол без сделок с запуска; перепроверка 21.10: правило держится, но сделок в 4–5 раз меньше, чем в описании (docs/research/listings_recent.md).",
                        since=_ms(lst.get("since")), updated=_ms(lst.get("asof")), source="paper.json"))
    out.append(_opp("polymarket", "Полимаркет", "Рынки прогнозов: проверены «быстрые» ставки на BTC, «облигации», арбитраж ДА+НЕТ и бумажный мейкер.",
                    "Not Active", result_test_note_ru="Был один бумажный прогон мейкера 19.10 (5,6 часа); постоянного демо-счёта нет — результата не показываем.",
                    risks_ru=["Комиссия тейкера до 3,5% на крипто-рынках.", "Доступ: polymarket.com заблокирован в Украине; VPN запрещён правилами.",
                              "Преимущество мейкера на бумаге не доказано."],
                    why_not_used_ru="Только исследование (docs/research/polymarket.md): после комиссий денег нет; реальные деньги — только после 4 недель бумаги и заключения юриста.",
                    source="docs/research/polymarket.md"))
    if real:
        out.append(_opp("okx_copy", "Копитрейдинг OKX (ведущий трейдер)", "Реальный счёт CRYPTO NOVA — ведущий трейдер OKX: копировщики повторяют его сделки, "
                        "ведущий получает долю их прибыли.", "Active", ops=(real or {}).get("copiers"), ops_ru="копировщиков сейчас",
                        risks_ru=["Доход только от прибыли копировщиков.", "Монеты только с парой к USDC на копирование не попадают."],
                        why_not_used_ru=None if (real or {}).get("copiers") else "Работает, но копировщиков пока нет.",
                        updated=_ms((real or {}).get("asof")), source="public_real.json"))
    if okx_sig:
        on = bool(okx_sig.get("on"))
        out.append(_opp("okx_signal", "Сигнал на OKX Signal Marketplace", okx_sig.get("about_ru") or "", "Active" if on else "Not Active",
                        ops=okx_sig.get("sent"), ops_ru="отправлено сигналов",
                        risks_ru=["Подписчики торгуют своими ботами: плечо и маржу ставят сами."],
                        why_not_used_ru=None if on else ("Выключено: " + (okx_sig.get("why_off") or UNKNOWN_RU)),
                        since=_ms(okx_sig.get("on_since")), updated=_ms(okx_sig.get("asof")), source="okx_signal.json"))
    order = {"Active": 0, "Approved": 1, "Testing": 2, "Monitoring": 3, "Paused": 4, "Not Active": 5}
    out.sort(key=lambda d: (order[d["status"]], d["name_ru"]))
    return {"asof": now, "statuses": [{"key": k, "name_ru": v} for k, v in OPP_STATUS_RU.items()], "items": out,
            "about_ru": "Другие способы заработка. «result_real_usd» — только реальные деньги, «result_test_usd» — демо или виртуальная оценка; "
                        "они никогда не складываются и не входят в публичный портфель."}


# ═════════════════════════════════════════════════ 4. system status ══════════════════════════════════════════════════
def _comp(key, name, status, last_ok, now, note, **kw) -> dict:
    return {"key": key, "name_ru": name, "status": status, "last_ok": _ms(last_ok), "age_min": _age_min(last_ok, now), "note_ru": note, **kw}


def _fresh(t, now, ok_min, stale_min=None) -> str:
    a = _age_min(t, now)
    if a is None:
        return "error"
    return "ok" if a <= ok_min else "stale"


def watch_last(path: str = "docs/research/watch_log.md") -> tuple[int | None, str]:
    """the researcher's last entry: («- 28.10 11:10 (Афины…) — тихо. …») → (ms UTC, the line) — None when unreadable"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if re.match(r"^- \d{2}\.\d{2} \d{2}:\d{2}", ln)]
    except Exception:  # noqa: BLE001
        return None, ""
    if not lines:
        return None, ""
    ln = lines[-1]
    m = re.match(r"^- (\d{2})\.(\d{2}) (\d{2}):(\d{2})", ln)
    year = dt.datetime.now(dt.timezone.utc).year
    t = dt.datetime(year, int(m.group(2)), int(m.group(1)), int(m.group(3)), int(m.group(4)), tzinfo=ATHENS)
    return int(t.timestamp() * 1000), ln[2:]


def system_status(out: str, now: int, lease: dict | None, api: tuple | None, prev: dict | None, watch: tuple,
                  guard: dict | None = None) -> dict:
    E = _load(out, "engine.json")
    comps = []
    # the pipeline's own state (scripts/ci_state_guard.py — 23.10 incident: the database was rolled back to 20.10)
    if guard:
        comps.append(_comp("state", "Состояние конвейера данных (data/agents.sqlite)", "error" if guard.get("stale") else "ok",
                           guard.get("checked") if not guard.get("stale") else guard.get("db_t"), now, guard.get("why_ru") or UNKNOWN_RU,
                           lag_h=guard.get("lag_h")))
    # engine cycle
    errs = E.get("errors") or []
    st = "error" if errs else _fresh(E.get("asof"), now, 10)
    cyc, iv = _num(E.get("cycle_sec")), _num(E.get("interval_sec"), 60)
    note = f"Цикл {E.get('cycle')}, {cyc} с из {iv:.0f}" if cyc is not None else "нет данных о цикле"
    if cyc is not None and iv and cyc > 0.9 * iv:
        note += " — цикл почти не укладывается в минуту"
    if errs:
        note += "; ошибки шагов: " + ", ".join(map(str, errs))
    slow = sorted(((k, _num(v.get("sec"), 0)) for k, v in (E.get("steps") or {}).items() if isinstance(v, dict) and not k.startswith("_")),
                  key=lambda x: -x[1])[:2]
    if slow:
        note += "; дольше всего: " + ", ".join(f"{k} {s:.0f} с" for k, s in slow)
    comps.append(_comp("engine", "Движок на сервере (демо-столы каждую минуту)", st, E.get("asof"), now, note + ".",
                       started=_ms(E.get("started"))))
    # GitHub sync of the engine
    comps.append(_comp("engine_sync", "Синхронизация движка с GitHub (раз в 2 минуты)", _fresh(E.get("asof"), now, 10), E.get("asof"), now,
                       "Свежий web/engine.json в репозитории — значит, сервер пушит свои файлы."))
    # autopilot lease / heartbeat
    if lease:
        until, t, stop = _ms(lease.get("until")), _ms(lease.get("t")), bool(lease.get("stop"))
        if stop:
            st, note = "stopped", "Глобальный стоп автопилота включён — ордеров нет (позиции держат свои стопы на OKX)."
        elif until and until > now:
            st, note = "ok", f"Замок у «{lease.get('holder')}» ({lease.get('node')}), действует до {dt.datetime.fromtimestamp(until / 1000, KYIV).strftime('%H:%M')} по Киеву; продлевается каждые ~5 минут."
        else:
            st, note = "stale", "Замок истёк: автопилот не продлевает его — ордеров нет, пока он не вернётся."
        comps.append(_comp("autopilot", "Автопилот OKX (замок / пульс)", st, t, now, note, node=lease.get("node")))
    else:
        comps.append(_comp("autopilot", "Автопилот OKX (замок / пульс)", "error", None, now, "Файл замка okx-lease не прочитан."))
    # public_real publish
    R = _load(out, "public_real.json")
    st = "stale" if R.get("stale") else _fresh(R.get("asof"), now, 30)
    comps.append(_comp("public_real", "Реальный счёт OKX → web/public_real.json (раз в 15 минут)", st, R.get("asof"), now,
                       "Публикует автопилот на сервере." + (" Файл помечен как устаревший." if R.get("stale") else "")))
    # the GitHub data pipeline
    S, V = _load(out, "status.json"), _load(out, "version.json")
    t_site = max(_ms(S.get("time")) or 0, _ms(V.get("time")) or 0) or None
    comps.append(_comp("site_pipeline", "Данные и сайт (GitHub, по расписанию раз в 30 минут)", _fresh(t_site, now, 75), t_site, now,
                       "Новости, исследования, спот-портфели, таблица, арбитраж, голосовой отчёт; затем публикация сайта."))
    P, I = _load(out, "portfolio.json"), _load(out, "ideas.json")
    t_i = _ms(I.get("asof"))
    st = "ok" if t_i and now - t_i <= H else ("stale" if t_i else "error")
    comps.append(_comp("spot_table", "Таблица «Что брать на спот» и портфель CRYPTO NOVA", st, min(t_i or 0, _ms(P.get("asof")) or 0) or None, now,
                       "Старше 3 часов — портфель не покупает новые монеты (правило портфеля)." if st != "ok" else "Свежая: портфель может покупать."))
    # hourly conveyor and the desk selector
    C, D = _load(out, "conveyor.json"), _load(out, "desk_select.json")
    comps.append(_comp("conveyor", "Конвейер стратегий и автовыбор стола (раз в час)", _fresh(min(_ms(C.get("asof")) or 0, _ms(D.get("asof")) or 0) or None, now, 130),
                       min(_ms(C.get("asof")) or 0, _ms(D.get("asof")) or 0) or None, now,
                       f"Автовыбор: {D.get('short_ru') or '—'}." if D else "web/desk_select.json нет."))
    A = _load(out, "autolab.json")
    comps.append(_comp("autolab", "Лаборатория (ночной прогон)", _fresh(A.get("asof"), now, 26 * 60), A.get("asof"), now,
                       f"Проверено вариантов: {A.get('tested')}, прошли: {A.get('passed')}." if A else "autolab.json нет."))
    # the researcher (hourly watch, docs/research/watch_log.md)
    wt, wl = watch
    comps.append(_comp("researcher", "Дежурный исследователь (раз в час)", _fresh(wt, now, 130) if wt else "error", wt, now,
                       (wl[:220] + ("…" if len(wl) > 220 else "")) if wl else "Журнал дежурства не прочитан."))
    # 28.10: «Иван тест» and «Мой портфель» — the read-only monitors (the server's timer since 28.10, before that the Mac mirror)
    t_iv, t_my = _ms(_load(out, "ivan.enc.json").get("asof")), _ms(_load(out, "myspot.enc.json").get("asof"))
    t_mon = max(t_iv or 0, t_my or 0) or None
    st = "ok" if t_mon and now - t_mon <= 45 * 60_000 else "stopped"
    comps.append(_comp("monitors", "Мониторы «Иван тест» и «Мой портфель» (только чтение, раз в 15 минут)", st, t_mon, now,
                       "Работают." if st == "ok" else
                       f"Не обновлялись с {_ddmm(t_mon) if t_mon else '—'}: web/ivan.enc.json и web/myspot.enc.json показывают старые данные. "
                       "Включить на сервере: ключи «только чтение» в /etc/cnq-okx.env и `okx_autopilot_server.sh monitors`."))
    # the Mac's Bybit demo mirror (it trades the demo account — stays on the Mac)
    t_mac = max(_ms(_load(out, "bybit.json").get("asof")) or 0, _ms(_load(out, "pulse.json").get("t")) or 0) or None
    st = "ok" if t_mac and now - t_mac <= 45 * 60_000 else "stopped"
    comps.append(_comp("mac_mirror", "Зеркало на Маке (Bybit демо)", st, t_mac, now,
                       "Работает." if st == "ok" else "Не обновляло web/bybit.json с " + (_ddmm(t_mac) if t_mac else "—") +
                       ": демо-счёт Bybit в панели показывает старые данные."))
    # the voice brief
    B = _load(out, "brief.json")
    if B:
        comps.append(_comp("voice", "Голосовой отчёт (brief.mp3)", _fresh(B.get("asof"), now, 75), B.get("asof"), now,
                           ("Озвучено запасным голосом: " + str(B.get("error"))[:160]) if B.get("error") else "Озвучено."))
    # the 2.0 API (probed from the pipeline)
    pa = {c.get("key"): c for c in (prev or {}).get("components") or [] if isinstance(c, dict)}.get("api") or {}
    if api is None:
        comps.append(_comp("api", "API приложения (/v2/health)", "stale", pa.get("last_ok"), now, "В этом прогоне не проверялся."))
    else:
        code, body = api
        ok = code == 200 and isinstance(body, dict) and body.get("ok", True) is not False
        comps.append(_comp("api", "API приложения (/v2/health)", "ok" if ok else "error", now if ok else pa.get("last_ok"), now,
                           f"HTTP {code}" + ("" if ok else f": {json.dumps(body, ensure_ascii=False)[:160]}")))
    worst = "error" if any(c["status"] == "error" for c in comps) else "stale" if any(c["status"] in ("stale", "stopped") for c in comps) else "ok"
    return {"asof": now, "overall": worst, "components": comps,
            "about_ru": "Только реальные отметки времени из файлов системы. age_min — на момент asof; панель пересчитывает возраст по last_ok. "
                        "Журналы сервера отсюда недоступны: что не видно в репозитории, здесь не показано."}


# ═════════════════════════════════════════════════ run ═══════════════════════════════════════════════════════════════
def _ivan_stats(out: str) -> dict | None:
    d = _load(out, "ivan_stats.json")
    return d if d.get("v") == 1 else None


def run(out: str = "web", conn=None, now: int | None = None, http: bool = True, watch_path: str = "docs/research/watch_log.md",
        guard_path: str = "data/state_guard.json") -> dict:
    now = int(now or time.time() * 1000)
    pf, ideas, memes, real = _load(out, "portfolio.json"), _load(out, "ideas.json"), _load(out, "memes.json"), _load(out, "public_real.json")
    paper = _load(out, "paper.json")
    docs = {}
    prev_sp = _load(out, "spot_pipeline.json")
    docs["spot_pipeline.json"] = spot_pipeline(pf, ideas, memes, real, prev_sp, okx_set(conn, now), ledger_actions(conn), now)
    docs["strategies.json"] = strategies(paper, _load(out, "conveyor.json"), _load(out, "desk_select.json"), pf, _load(out, "app_signals.json"),
                                         _load(out, "whales_top.json"), memes, _load(out, "capital.json"), _load(out, "altseason.json"),
                                         _load(out, "ivan.enc.json"), _ivan_stats(out), real, now, conn)
    docs["opportunities.json"] = opportunities(_load(out, "tri.json"), _load(out, "arb.json"), _load(out, "funding.json"), _load(out, "basis.json"),
                                               _load(out, "yield.json"), paper, real, _load(out, "okx_signal.json"), now)
    lease = api = None
    if http:
        try:
            code, lease = _get_json(LEASE_URL)
            lease = lease if code == 200 and isinstance(lease, dict) else None
        except Exception:  # noqa: BLE001
            lease = None
        try:
            api = _get_json(API_HEALTH_URL)
        except urllib.error.HTTPError as e:
            api = (e.code, {"error": str(e)[:120]})
        except Exception as e:  # noqa: BLE001
            api = (0, {"error": repr(e)[:120]})
    guard = _load(os.path.dirname(guard_path) or ".", os.path.basename(guard_path))
    docs["system_status.json"] = system_status(out, now, lease, api, _load(out, "system_status.json"), watch_last(watch_path), guard or None)
    for name, d in docs.items():
        _write(out, name, d)
    return docs

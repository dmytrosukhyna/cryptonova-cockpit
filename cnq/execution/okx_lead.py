"""«Ведущий трейдер» на споте OKX (22.10.2026, owner: the autopilot's sub-account became an OKX SPOT lead trader — the spot trades of
«CRYPTO NOVA портфель» must be copyable). Used by okx_autopilot (orders, plan, check), myspot_watch.read_raw (accounting) and
myspot_sub / public_real (the card and the public line).

When OKX reports a spot lead role (account config spotRoleType 1, or /api/v5/copytrading/config → details instType SPOT,
roleType 1) and OKX_SPOT_LEAD is not «off»:
  • lead instruments = the portfolio's coins that OKX supports for spot copy (GET /api/v5/copytrading/instruments?instType=SPOT),
    as {COIN}-USDT, plus every instrument with an open lead position (never dropped while one is open); set with POST
    /api/v5/copytrading/set-instruments (instType SPOT) when they differ (the portfolio changed) and at least once a day. When OKX
    refuses the whole list, the coins are added one by one; a refused coin stays an ordinary trade;
  • buys and sells of the portfolio on a lead instrument go with tdMode «spot_isolated» (OKX: for SPOT lead trading) — the copiers
    repeat them. A sale sells the lead positions first, then the coins bought before the switch (ordinary spot). A full exit that
    OKX refuses as a lead order closes the lead positions (POST /api/v5/copytrading/close-subposition, instType SPOT, limit price);
  • NOT copied (ordinary «cash» trades, named in the plan «не копируется: …»): a coin OKX does not support for copy, a coin traded
    through USDC, a coin OKX did not accept as a lead instrument, and the «Импульс BTC» active spot (its own sleeve; kept simple);
  • coins bought before the switch stay as they are (ordinary spot, «не лидерские» in the plan). `--spot-lead-migrate` shows the
    one-time move into lead positions (sell the ordinary coins, buy them back as lead): ≈ 2 taker fees + the spread; with
    `--live` it asks ДА. The autopilot never runs it by itself;
  • accounting: lead positions are counted exactly like ordinary coins in every share, budget and «СОВПАДАЕТ» (read_raw → merge):
    a coin = max(eq, cashBal, availBal + lead) on the trading account, never twice; the lead part is never sold as «cash».
    A lead account whose lead positions can not be read places no spot orders in that pass.
OKX_SPOT_LEAD=off: no lead instruments are set, every order is «cash»; open lead positions are still counted (not sold).
Nothing here moves money between accounts or withdraws. Stdlib only."""
from __future__ import annotations

import math
import os
import time

from . import myspot_watch as ms

TD = "spot_isolated"                               # the order's tdMode for SPOT lead trading
DAY_MS = 86_400_000
CONFIG = "/api/v5/copytrading/config"
INSTS = "/api/v5/copytrading/instruments"
SET = "/api/v5/copytrading/set-instruments"
SUBS = "/api/v5/copytrading/current-subpositions"
CLOSE = "/api/v5/copytrading/close-subposition"
READS = (CONFIG, INSTS, SUBS)                      # the GET paths (myspot_sub's read-only list)
PROFILE = "https://www.okx.com/copy-trading/account/"


def _env(env=None) -> dict:
    return os.environ if env is None else env


def disabled(env=None) -> bool:
    return (_env(env).get("OKX_SPOT_LEAD") or "").strip().lower() in ("off", "0", "no", "false")


def _first(d) -> dict:
    return d[0] if isinstance(d, list) and d and isinstance(d[0], dict) else {}


def spot_role(cfg: dict | None) -> bool:
    return str((cfg or {}).get("spotRoleType") or "") == "1"


def copy_url(code) -> str | None:
    """The public lead-trader page from the copy-trading uniqueCode (letters and digits only)."""
    c = str(code or "").strip()
    return PROFILE + c if c and c.isalnum() and len(c) <= 64 else None


def roles(cfg: dict | None, ct) -> dict:
    """The lead roles from the account config and /api/v5/copytrading/config: {"spot", "fut", "copiers": {SPOT, SWAP}, "url"}."""
    cfg = cfg or {}
    c0 = _first(ct)
    by = {str(d.get("instType") or "").upper(): d for d in (c0.get("details") or []) if isinstance(d, dict)}

    def n(it):
        v = ms._f((by.get(it) or {}).get("copyTraderNum"))
        return int(v) if v is not None else None
    spot = spot_role(cfg) or str((by.get("SPOT") or {}).get("roleType") or "") == "1"
    fut = str(cfg.get("roleType") or "") == "1" or str((by.get("SWAP") or {}).get("roleType") or "") == "1"
    return {"spot": spot, "fut": fut, "copiers": {"SPOT": n("SPOT"), "SWAP": n("SWAP")},
            "url": copy_url(c0.get("uniqueCode")) if (spot or fut) else None}


def held(get, pages: int = 10) -> dict:
    """The open lead spot positions: {COIN: {"qty", "avail", "insts", "subs": [(subPosId, instId, qty)]}}. Raises when unreadable."""
    out, after = {}, None
    for _ in range(pages):
        d = get(SUBS, {"instType": "SPOT", **({"after": after} if after else {})}) or []
        for s in d:
            inst = str(s.get("instId") or "")
            q = ms._f(s.get("subPos")) or 0.0
            if not inst or q <= 0:
                continue
            av = ms._f(s.get("availSubPos"))
            a = out.setdefault(inst.split("-")[0].upper(), {"qty": 0.0, "avail": 0.0, "insts": [], "subs": []})
            a["qty"] += q
            a["avail"] += q if av is None else av
            if inst not in a["insts"]:
                a["insts"].append(inst)
            a["subs"].append((str(s.get("subPosId") or ""), inst, q))
        after = str((d[-1] if d else {}).get("subPosId") or "")
        if len(d) < 100 or not after:
            break
    return out


def raw_lead(get, cfg: dict | None, trading: list) -> tuple[dict, str | None]:
    """For read_raw: the lead positions when the account is a spot lead (or the balance shows spotIsoBal). (held, error)."""
    iso = any((ms._f(d.get("spotIsoBal")) or 0) > 0 for d in trading or [])
    if not (spot_role(cfg) or iso):
        return {}, None
    try:
        return held(get), None
    except Exception as e:  # noqa: BLE001
        return {}, str(e)[:160]


def merge(trading: list, lead: dict) -> list:
    """The trading-account details with the lead coins counted once: eq = max(eq, cashBal, availBal + lead), availBal = the ordinary
    part only (never more than eq − lead). A coin held only as lead positions gets its own row."""
    out, seen = [], set()
    for d in trading or []:
        c = str(d.get("ccy") or "").upper()
        L = max((lead.get(c) or {}).get("qty", 0.0), ms._f(d.get("spotIsoBal")) or 0.0)
        if L > 0:
            E, C, A = ms._f(d.get("eq")) or 0.0, ms._f(d.get("cashBal")) or 0.0, ms._f(d.get("availBal"))
            A = (C if A is None else A)
            tot = max(E, C, A + L)
            u = ms._f(d.get("eqUsd"))
            d = {**d, "eq": repr(tot), "cashBal": repr(max(C, tot)), "availBal": repr(max(0.0, min(A, tot - L))), "leadQty": L,
                 "eqUsd": repr(u * tot / E) if u and E else ""}
            seen.add(c)
        out.append(d)
    for c, h in lead.items():
        if c not in seen and h.get("qty", 0) > 0:
            out.append({"ccy": c, "eq": repr(h["qty"]), "cashBal": repr(h["qty"]), "availBal": "0", "leadQty": h["qty"]})
    return out


def context(get, cfg: dict | None, env=None, raw: dict | None = None) -> dict:
    """Everything the plan needs: roles, open lead positions, the instruments OKX supports / has set, OKX_SPOT_LEAD."""
    cfg = cfg or {}
    try:
        ct = get(CONFIG)
    except Exception:  # noqa: BLE001
        ct = None
    r = roles(cfg, ct)
    ti = cfg.get("spotTraderInsts") or []
    ctx = {"role": r, "off": disabled(env), "active": r["spot"] and not disabled(env), "supported": set(), "fails": {},
           "enabled": set(ti if isinstance(ti, list) else [x for x in str(ti).split(",") if x]),
           "held": (raw or {}).get("lead") or {}, "err": (raw or {}).get("lead_err")}
    if r["spot"] and (raw is None or not (spot_role(cfg) or ctx["held"] or ctx["err"])):
        try:                                       # the role came from copytrading/config only: read_raw did not read them
            ctx["held"], ctx["fresh"] = held(get), raw is not None
        except Exception as e:  # noqa: BLE001
            ctx["err"] = str(e)[:160]
    if r["spot"]:
        try:
            d = get(INSTS, {"instType": "SPOT"}) or []
            ctx["supported"] = {str(x.get("instId")) for x in d if x.get("instId")}
            en = {str(x.get("instId")) for x in d if str(x.get("enabled")).lower() == "true"}
            if en or not ctx["enabled"]:
                ctx["enabled"] = en
        except Exception as e:  # noqa: BLE001
            ctx["fails"]["*"] = f"список монет для копирования не прочитался: {str(e)[:120]}"
    return ctx


def want(ctx: dict, coins, quote: str = "USDT") -> set:
    """The lead instruments the account should have: everything already set (never shrunk — 22.10 live: OKX had all 245 supported
    coins enabled) + the portfolio's coins (direct USDT pair) OKX supports + every open lead position's instrument."""
    d = set(ctx.get("enabled") or set()) | {f"{c}-{quote}" for c in coins if f"{c}-{quote}" in ctx["supported"]}
    for h in (ctx.get("held") or {}).values():
        d |= set(h["insts"])
    return d


def add_insts(cl, inst_type: str, cur: set, missing: set) -> tuple[set, dict]:
    """POST set-instruments with the FULL list (what is set + the missing ones) — never removes one. Refused as a whole → the
    missing ones one by one. Returns (the instruments OKX reports enabled afterwards, {instId: why refused})."""
    fails = {}

    def put(insts):
        cl.call("POST", SET, body={"instType": inst_type, "instId": ",".join(sorted(insts))})
    try:
        put(cur | missing)
    except ms.OkxError as e:
        have = set(cur)
        for i in sorted(missing):
            try:
                put(have | {i})
                have.add(i)
            except ms.OkxError as e2:
                fails[i] = (e2.msg or e2.code or str(e))[:120]
    try:
        d = cl.call("GET", INSTS, {"instType": inst_type}) or []
        en = {str(x.get("instId")) for x in d if str(x.get("enabled")).lower() == "true"}
    except ms.OkxError:
        en = (cur | missing) - set(fails)
    return en, fails


def sync(cl, ctx: dict, desired: set, st: dict, now: int, log=None) -> set:
    """Spot: every coin OKX supports for spot copy stays / becomes a lead instrument (22.10 owner: everything copyable), never
    removed. Missing supported coins → one POST with the full list (at most once a day when OKX refuses it); a missing PORTFOLIO
    coin is then added one by one (add_insts). Nothing missing → no call. Returns (and keeps in ctx) what OKX has now."""
    if not ctx["active"]:
        return ctx["enabled"]
    st["lead_t"] = now
    cur = set(ctx["enabled"])
    every = (set(ctx["supported"]) | set(desired)) - cur
    if not every or (not cur and "*" in ctx["fails"]):     # nothing missing, or OKX's lists unreadable → no POST that could shrink
        return ctx["enabled"]
    en, fails, added = cur, {}, set()
    if every - set(desired) and now - int(st.get("lead_full_t") or 0) >= DAY_MS:
        try:
            cl.call("POST", SET, body={"instType": "SPOT", "instId": ",".join(sorted(cur | every))})
            en, added = cur | every, every
        except ms.OkxError as e:
            st["lead_full_t"] = now
            if log:
                log({"ev": "lead_instruments_full_refused", "type": "SPOT", "n": len(cur | every), "error": str(e)[:160]})
    missing = set(desired) - en
    if missing:
        en, fails = add_insts(cl, "SPOT", en, missing)
        added |= missing - set(fails)
    elif added:
        try:
            d = cl.call("GET", INSTS, {"instType": "SPOT"}) or []
            en = {str(x.get("instId")) for x in d if str(x.get("enabled")).lower() == "true"} or en
        except ms.OkxError:
            pass
    ctx["enabled"], ctx["fails"] = en, {**{k: v for k, v in ctx["fails"].items() if k == "*"}, **fails}
    st.update({"lead_added": sorted(added)[:300], "lead_fail": fails})
    if log:
        log({"ev": "lead_instruments", "type": "SPOT", "added": sorted(added), "failed": fails, "n": len(en)})
    return en


# Why a coin / a position is not copied — one code, one plain sentence (the plan, the card, public_real.json, the app)
REASON_RU = {
    "usdc_only": "не добавлено в копируемый портфель: OKX копирует только пары к USDT, а у этой монеты есть только пара к USDC",
    "not_supported": "не добавлено в копируемый портфель: OKX не даёт копировать эту монету",
    "legacy": "не добавлено в копируемый портфель: монета куплена до включения режима ведущего трейдера — это обычный спот",
    "ordinary": "не добавлено в копируемый портфель: OKX не принял её в список ведущего трейдера — это обычная сделка",
}


def reason_text(code: str | None, detail: str | None = None) -> str:
    t = REASON_RU.get(code or "", "")
    return t + (f" (ответ OKX: {detail})" if t and detail else "")


def _why_not(c: str, via, inst: str, ctx: dict, en: set) -> str | None:
    """The plan's decision for a portfolio coin: None = copied, else a REASON_RU code."""
    if via:
        return "usdc_only"
    if inst in en:
        return None
    if inst not in ctx["supported"]:
        return "not_supported"
    return "ordinary"


def coin_copy(c: str, ctx: dict, qty: float, pairs: set, quote: str = "USDT") -> dict:
    """One held spot coin for the card / public_real: {"copy", "copy_reason", "copy_pct"} — copied = held as lead positions
    (the whole coin; copy_pct = the lead share of it, %). The same codes as the plan (_why_not) + «legacy» for coins held normally."""
    r = ctx["role"]
    L = ((ctx.get("held") or {}).get(c) or {}).get("qty", 0.0) if r["spot"] else 0.0
    pct = round(min(100.0, L / qty * 100), 1) if qty else 0.0
    inst = f"{c}-{quote}"
    if L > 0 and L >= qty * 0.99:
        return {"copy": True, "copy_reason": None, "copy_pct": 100.0}
    if inst not in pairs and f"{c}-USDC" in pairs:
        code = "usdc_only"
    elif not r["spot"] or ctx["off"]:
        code = "ordinary"
    elif ctx["supported"] and inst not in ctx["supported"]:
        code = "not_supported"
    elif inst in ctx.get("fails", {}):
        code = "ordinary"
    else:
        code = "legacy"
    return {"copy": False, "copy_reason": code, "copy_pct": pct}


def swap_copy(inst: str, fut_lead: bool, lead_swaps: set | None) -> dict:
    """One futures position: copied = it has lead sub-positions; otherwise «ordinary». lead_swaps None = unreadable."""
    if fut_lead and lead_swaps is None:
        return {"copy": None, "copy_reason": None}
    ok = bool(fut_lead and inst in lead_swaps)
    return {"copy": ok, "copy_reason": None if ok else "ordinary"}


def mark(b: dict, ctx: dict, en: set | None = None) -> dict:
    """b["sells0"] / b["buys0"] → b["sells"] / b["buys"]: lead instruments get tdMode spot_isolated (copied); a sale takes the lead
    positions first, then the ordinary coins; everything else stays «cash». Fills b["lead"] (the plan's lines). Idempotent."""
    from . import okx_autopilot as ap
    en = set(ctx["enabled"] if en is None else en)
    act = ctx["active"]
    left = {c: h["avail"] for c, h in (ctx.get("held") or {}).items()} if act else {}
    norm = dict(b.get("_avail") or {})
    sells = []
    for o in b["sells0"]:
        c, q, lot, mn = o["ccy"], float(o["sz"]), float(o["lot"]), float(o["min"])
        ql = 0.0
        if act and not o.get("via") and o["inst"] in en and left.get(c, 0.0) > 0:
            ql = ap.floor_to(min(q, left[c]), lot)
            if ql >= mn and ql > 0:
                sells.append({**o, "sz": ap.fmt(ql, o["lot"]), "usd": round(o["usd"] * ql / q, 2), "td": TD, "lead": True})
                left[c] -= ql
            else:
                ql = 0.0
        rest = ap.floor_to(min(q - ql, norm.get(c, 0.0)) if ctx["held"] else q - ql, lot)
        if rest >= mn and rest > 0:
            norm[c] = norm.get(c, 0.0) - rest
            sells.append({**o, "sz": ap.fmt(rest, o["lot"]), "usd": round(o["usd"] * rest / q, 2),
                          **({"legacy": True} if act and not o.get("via") and o["inst"] in en else {})})
    buys = [{**o, "td": TD, "lead": True} if act and not o.get("via") and o["inst"] in en else dict(o) for o in b["buys0"]]
    b["sells"], b["buys"] = sells, buys
    routes = b.get("_routes") or {}
    coins = [r["ccy"] for r in b.get("targets") or []]
    copied, nc = [], {}
    for c in coins:
        via, inst = routes.get(c, (None, f"{c}-{b['quote']}"))
        why = _why_not(c, via, inst, ctx, en)
        if why:
            nc[c] = why
        else:
            copied.append(c)
    px = b.get("_px") or {}
    legacy = {c: round(norm_q, 8) for c, norm_q in (b.get("_avail") or {}).items()
              if c in copied and norm_q > 0 and norm_q * (px.get(c) or 0) >= 1.0}
    b["lead"] = {"active": act, "spot": ctx["role"]["spot"], "fut": ctx["role"]["fut"], "off": ctx["off"], "copied": copied,
                 "not_copied": nc, "legacy": legacy, "legacy_usd": round(sum(q * (px.get(c) or 0) for c, q in legacy.items()), 2),
                 "lead_qty": {c: round(h["qty"], 8) for c, h in (ctx.get("held") or {}).items()}, "copiers": ctx["role"]["copiers"],
                 "url": ctx["role"].get("url"), "err": ctx.get("err"), "fails": dict(ctx.get("fails") or {})}
    return b


def copiers_text(role: dict) -> str:
    c = role.get("copiers") or {}
    parts = []
    if role.get("spot") and c.get("SPOT") is not None:
        parts.append(str(c["SPOT"]) if not role.get("fut") else f"спот {c['SPOT']}")
    if role.get("fut") and c.get("SWAP") is not None:
        parts.append(str(c["SWAP"]) if not role.get("spot") else f"фьючерсы {c['SWAP']}")
    return ("копируют: " + " · ".join(parts)) if parts else ""


def role_line(role: dict, n: int | None, off: bool = False) -> str:
    """«ведущий трейдер: спот — да (N монет копируются) · фьючерсы — нет · копируют: K»."""
    if role.get("spot"):
        spot = ("да, но режим выключен строкой OKX_SPOT_LEAD=off — сделки не копируются" if off else
                f"да ({n} {_coins(n)} копируются)" if n is not None else "да")
    else:
        spot = "нет"
    cp = copiers_text(role)
    return f"ведущий трейдер: спот — {spot} · фьючерсы — {'да' if role.get('fut') else 'нет'}" + (f" · {cp}" if cp else "")


def _coins(n) -> str:
    n = abs(int(n or 0))
    return "монета" if n % 10 == 1 and n % 100 != 11 else "монеты" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "монет"


def plan_lines(b: dict) -> list[str]:
    """The plan's lead block (plain Russian, digits)."""
    ld = b.get("lead") or {}
    if not ld:
        return []
    role = {"spot": ld.get("spot"), "fut": ld.get("fut"), "copiers": ld.get("copiers") or {}}
    L = ["  " + role_line(role, len(ld.get("copied") or []), ld.get("off")).replace("ведущий трейдер", "ВЕДУЩИЙ ТРЕЙДЕР", 1)]
    if not ld.get("spot"):
        return L
    if ld.get("err"):
        L.append(f"  !!! лидерские позиции не прочитались ({ld['err']}) — спот-ордеров в этом проходе нет")
    if ld.get("active"):
        if ld.get("copied"):
            L.append("  копируются (ордера «spot_isolated»): " + ", ".join(ld["copied"]))
        for c, w in (ld.get("not_copied") or {}).items():
            L.append(f"  {c}: " + reason_text(w, (ld.get("fails") or {}).get(f"{c}-USDT") if w == "ordinary" else None))
        if ld.get("legacy"):
            L.append("  не лидерские (куплены до включения — обычный спот, копировщики их не получают): "
                     + ", ".join(f"{c} {q:g}" for c, q in ld["legacy"].items()) + f" ≈ {_usd(ld.get('legacy_usd') or 0)}. "
                     "Остаются как есть; новые покупки и продажи — лидерские (продажа — сначала лидерские позиции). "
                     "Переложить их разом: --spot-lead-migrate (только план; с --live спросит ДА).")
    if ld.get("lead_qty"):
        L.append("  лидерские позиции сейчас: " + ", ".join(f"{c} {q:g}" for c, q in ld["lead_qty"].items())
                 + " — считаются в долях, бюджетах и «СОВПАДАЕТ» как обычные монеты")
    if ld.get("active"):
        L.append("  Импульс BTC (активный спот) тоже лидерский: покупки и выходы на монетах к USDT, которые OKX даёт копировать")
    if ld.get("url"):
        L.append(f"  профиль ведущего трейдера: {ld['url']}")
    return L


def _usd(v: float) -> str:
    return f"${v:,.2f}".replace(",", " ")


def card(ctx: dict) -> dict:
    """For the encrypted card (myspot_sub) and public_real: the role, the copied instruments, the copiers, the public link."""
    r = ctx["role"]
    n = len(ctx.get("enabled") or []) if r["spot"] and not ctx["off"] else None
    cp = r.get("copiers") or {}
    tot = sum(v for k, v in cp.items() if v is not None and ((k == "SPOT" and r["spot"]) or (k == "SWAP" and r["fut"])))
    return {"spot": r["spot"], "fut": r["fut"], "off": ctx["off"], "n": n, "copiers": tot if (r["spot"] or r["fut"]) else None,
            "url": r.get("url"), "line": role_line(r, n, ctx["off"]),
            "lead_qty": {c: round(h["qty"], 8) for c, h in (ctx.get("held") or {}).items()}}


def check_lines(cl, cfg: dict, env=None) -> list[str]:
    """`--check`: the spot lead role, how many instruments are set, the copiers and the public profile link."""
    try:
        ctx = context(lambda p, q=None: cl.call("GET", p, q), cfg, env)
    except Exception as e:  # noqa: BLE001
        return [f"  ведущий трейдер: не прочиталось ({str(e)[:120]})"]
    c = card(ctx)
    out = ["  " + c["line"]]
    if ctx["role"]["spot"]:
        out.append(f"  спот-лидер: монет, которые OKX даёт копировать, {len(ctx['supported'])}; выставлено лидерскими сейчас: "
                   + (", ".join(sorted(ctx["enabled"])) or "ни одной") + " — автопилот выставит монеты портфеля сам при ребалансе"
                   + (" (выключено: OKX_SPOT_LEAD=off)" if ctx["off"] else ""))
        if ctx.get("err"):
            out.append(f"  !!! лидерские позиции не прочитались: {ctx['err']} — спот-ордеров не будет, пока не прочитаются")
    if c["url"]:
        out.append(f"  профиль ведущего трейдера (ссылка для «Копировать CRYPTO NOVA»): {c['url']}")
    return out


def lead_avail(cl, inst_id: str) -> float:
    """The lead quantity of one spot instrument that can be sold now (its open lead positions)."""
    d = cl.call("GET", SUBS, {"instType": "SPOT", "instId": inst_id}) or []
    return sum((ms._f(s.get("availSubPos")) if ms._f(s.get("availSubPos")) is not None else ms._f(s.get("subPos")) or 0.0)
               for s in d if s.get("instId") == inst_id)


def close_out(cl, o: dict, err: Exception, log=None, only: set | None = None) -> dict:
    """A full exit OKX refused as a lead order: close the lead positions of that instrument (close-subposition, SPOT, limit at
    the order's price — never below it); only = just the ones opened by these order ids (the active spot's own). Returns _fill's
    shape (filled = the closed quantity)."""
    d = cl.call("GET", SUBS, {"instType": "SPOT", "instId": o["inst"]}) or []
    q, ids = 0.0, []
    for s in d:
        sid, sq = str(s.get("subPosId") or ""), ms._f(s.get("availSubPos")) or ms._f(s.get("subPos")) or 0.0
        if not sid or sq <= 0 or (only is not None and str(s.get("openOrdId") or "") not in only):
            continue
        cl.call("POST", CLOSE, body={"instType": "SPOT", "subPosId": sid, "ordType": "limit", "px": o["px"]})
        q += sq
        ids.append(sid)
    if log:
        log({"ev": "lead_close", "inst": o["inst"], "subs": len(ids), "qty": q, "why": f"лидерская продажа отклонена: {str(err)[:120]}"})
    px = float(o["px"])
    return {"ordId": None, "state": "lead_closed" if ids else "not_sent", "filled": q, "avg_px": px if q else 0.0,
            "usd": round(q * px, 2), "fee": 0.0, "full": bool(ids)}


# ── the one-time move of the coins bought before the switch (never run by the autopilot itself) ─────────────────────────────
def migrate_plan(cl, b: dict, env=None) -> dict:
    """For every copied coin: sell its ordinary coins («cash») and buy the same quantity back as a lead position. Places nothing."""
    from . import okx_autopilot as ap
    lim, fee = ap.limits(env), ap.taker_fee(cl, env)
    sells, buys, spread = [], [], 0.0
    for c, q0 in (b.get("lead") or {}).get("legacy", {}).items():
        inst_id = f"{c}-{b['quote']}"
        info, tk = cl.instrument(inst_id), cl.ticker(inst_id) or {}
        bid, ask = ms._f(tk.get("bidPx")) or ms._f(tk.get("last")), ms._f(tk.get("askPx")) or ms._f(tk.get("last"))
        if not info or not bid or not ask:
            continue
        lot, mn, tick = float(info["lotSz"]), float(info["minSz"]), float(info["tickSz"])
        q = ap.floor_to(q0, lot)
        if q < mn or q * bid < ap.MIN_ORDER:
            continue
        n = max(1, math.ceil(q * bid / lim["max_order"]))
        part = ap.floor_to(q / n, lot)
        parts = [p for p in [part] * (n - 1) + [ap.floor_to(q - part * (n - 1), lot)] if p >= mn]
        spx, bpx = ap.fmt(ap.floor_to(bid * (1 - lim["slip"]), tick), info["tickSz"]), ap.fmt(ap.ceil_to(ask * (1 + lim["slip"]), tick), info["tickSz"])
        for p in parts:
            base = {"ccy": c, "inst": inst_id, "sz": ap.fmt(p, info["lotSz"]), "lot": info["lotSz"], "min": mn, "via": None,
                    "pxq": b["quote"], "target_pct": 0.0, "now_pct": 0.0, "why": "перевод в лидерские позиции"}
            sells.append({**base, "side": "sell", "px": spx, "usd": round(p * bid, 2), "act": "trim"})
            buys.append({**base, "side": "buy", "px": bpx, "usd": round(p * ask, 2), "ask": ask, "act": "buy", "core": False,
                         "td": TD, "lead": True})
        spread += q * (ask - bid)
    turn = sum(o["usd"] for o in sells + buys)
    return {**b, "sells": sells, "buys": buys, "mig_usd": round(sum(o["usd"] for o in sells), 2), "fee_usd": round(turn * fee, 2),
            "fee_rate": fee, "mig_spread": round(spread, 2)}


def migrate_text(m: dict) -> str:
    if not m["sells"]:
        return "Перевод в лидерские позиции: переводить нечего — монет, купленных до включения, на счёте нет (или они меньше $10)."
    L = [f"ПЕРЕВОД В ЛИДЕРСКИЕ ПОЗИЦИИ (один раз): {len(m['sells'])} пар ордеров на {_usd(m['mig_usd'])}"]
    for s_, b_ in zip(m["sells"], m["buys"]):
        L.append(f"  {s_['ccy']}: ПРОДАТЬ {s_['sz']} обычных (лимит не ниже {s_['px']}), потом КУПИТЬ {b_['sz']} лидерской позицией "
                 f"(лимит не выше {b_['px']})")
    L.append(f"  Стоимость: комиссия ≈ {_usd(m['fee_usd'])} (2 сделки × {ms._p(round(m['fee_rate'] * 100, 2))}%) + спред ≈ "
             f"{_usd(m['mig_spread'])}; цена может уйти между продажей и покупкой (лимит IOC не дальше 0,4% от цены). "
             "Доли портфеля не меняются; копировщики после этого получат эти монеты как покупки ведущего.")
    return "\n".join(L)


def migrate(env=None, opener=None, model=None, confirm=None, now: int | None = None, sleep=time.sleep, log_path: str | None = None) -> str:
    """`--spot-lead-migrate`: the plan (confirm None) or, with confirm(text) → True, the orders: ordinary sells, then lead buys —
    under the kill switch, the lease, the per-order / per-day caps and CNQ_LIVE_TRADING=yes, like every autopilot order."""
    from . import okx_autopilot as ap
    from . import okx_lease as lease
    now = int(now or time.time() * 1000)
    st = ap.load_state(env)
    cl = ap.connect(env, opener, st, sleep)
    b = ap.build(cl, env, model)
    if not (b.get("lead") or {}).get("active"):
        return "Перевод не нужен: OKX не показывает роль ведущего трейдера на споте (или OKX_SPOT_LEAD=off)."
    if (b.get("lead") or {}).get("err"):
        return "Лидерские позиции не прочитались — перевод не делаю."
    m = migrate_plan(cl, b, env)
    text = migrate_text(m)
    if confirm is None or not m["sells"]:
        return text + ("\nЭто только план, ордеров нет. Выполнить: --spot-lead-migrate --live (спросит ДА)." if m["sells"] else "")
    if ap.stopped(env):
        return text + "\nВключён стоп (--stop) — ордера не ставлю."
    perm = ap.perm_of(cl.config())
    if "withdraw" in perm or "trade" not in perm:
        return text + "\nКлюч не подходит (нужны «Чтение» + «Торговля» без «Вывода») — ордеров нет."
    bad = ap.world_ok(cl, env)
    if bad:
        return text + "\n" + bad
    g = lease.gate(env, now)
    if not g["ok"]:
        return text + f"\nЗамок автопилота: {g['why']} — ордеров нет. Остановите автопилот (на сервере: sudo systemctl stop cnq-okx-autopilot) и повторите."
    cl.lease_deadline = lease.deadline(g, now)
    ctx = b["_ctx"]
    en = sync(cl, ctx, want(ctx, b["lead"]["copied"] + list(b["lead"]["legacy"]), b["quote"]), st, now,
              lambda ev: ap.log(ev, log_path))
    m["buys"] = [o for o in m["buys"] if o["inst"] in en]
    m["sells"] = [o for o in m["sells"] if o["inst"] in en]
    if not confirm(text):
        return "Отменено — ордеров нет."
    ap.log({"ev": "lead_migrate", "usd": m["mig_usd"], "pairs": len(m["sells"])}, log_path)
    res = ap.execute(cl, m, st, env, now, sleep, log_path)
    ap.save_state(st, env)
    return (text + f"\nГотово: продано обычных {_usd(res['sold_usd'])}, куплено лидерскими {_usd(res['bought_usd'])}, ордеров {res['orders']}, "
            f"ошибок {res['failed']}." + (" Дневной лимит достигнут — остальное повторите завтра." if res["limit"] else ""))

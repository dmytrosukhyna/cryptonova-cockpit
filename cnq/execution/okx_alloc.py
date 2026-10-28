"""«Ваши деньги = система CRYPTO NOVA» (22.10.2026, owner 09:56) — how the owner's OKX sub-account is split and what its spot part
holds. «Наша система» = what the closed system's header shows: FUTURES · CRYPTO NOVA (отобранные сигналы) and SPOT · CRYPTO NOVA
портфель. No DeFi or yield sleeves on this account.

  spot     OKX_SPOT_SHARE (0.65) — mirrors web/portfolio.json → portfolios.ideas («Наш портфель CRYPTO NOVA»): every coin at
           its weight in that portfolio, its cash rule (≥ 10 %) included; its stops, take-profits and sells reach the account as
           the portfolio's own changes (okx_autopilot rebalances when they happen).
  core     OKX_CORE_SHARE (0.10) — owner's decision of 28.10 (real money, answered explicitly): the 10 % freed from the futures
           go to the core BTC / ETH / SOL in the core's plan proportions 30 / 10 / 5 (CORE_PLAN, the same as the portfolio's
           core and its «кэш → ядро» rule) on top of what the spot portfolio holds.
  futures  OKX_FUT_SHARE (0.15; 0.25 until 28.10, owner's decision of 28.10) — the budget of okx_futures.py (the CRYPTO NOVA
           futures desks): this share of the STARTING capital + the futures' own result (fut_budget), never below the margin of
           the open positions — not a share of the account's current value any more (owner's decision of 28.10).
  active   OKX_ACT_SHARE (0.10) — okx_active.py follows the «Импульс BTC» spot desk; between its trades the sleeve waits in BTC
           (owner's decision of 28.10; USDT for 3 days after the desk's exit — okx_active.py).
  The rest of 100 % (if the shares add up to less) is a USDT reserve. Nothing is moved between accounts by code.

The account's target for a coin = spot share × the coin's weight in the public portfolio. Symbols: a public ticker is mapped to an
OKX spot pair only when the pair exists AND its price matches the public portfolio's price within 25 % (the public portfolio's own
rule against a different token with the same ticker); 1000BONK → BONK-USDT (price × 1000). A coin that cannot be matched is never
guessed: its share stays in USDT and the plan says so. Not financial advice. Stdlib only."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

SRC = os.environ.get("OKX_PUBLIC_SRC") or os.environ.get("MYSPOT_MODEL_SRC") or \
    "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/"
# Owner's decision of 28.10 (real money, answered explicitly): futures 25 % → 15 %, the freed 10 % → the core BTC / ETH / SOL.
# Account: spot portfolio 65 % + extra core 10 % + futures 15 % + «Импульс BTC» 10 %. Leverage stays 2x (max 3x, okx_futures).
SHARES = {"spot": 0.65, "core": 0.10, "futures": 0.15, "active": 0.10}
ENV_KEYS = {"spot": "OKX_SPOT_SHARE", "core": "OKX_CORE_SHARE", "futures": "OKX_FUT_SHARE", "active": "OKX_ACT_SHARE"}
PRICE_TOL = 0.25
CORE = ("BTC", "ETH", "SOL")
CORE_PLAN = {"BTC": 0.30, "ETH": 0.10, "SOL": 0.05}     # = cnq/agents/spot_profiles.py IDEAS["core_w"] (a test keeps them equal)
NAMES = {"spot": "Спот · CRYPTO NOVA портфель", "core": "Ядро BTC / ETH / SOL (доп., 30 / 10 / 5)",
         "futures": "Фьючерсы · CRYPTO NOVA (отобранные сигналы)", "active": "Активный спот · Импульс BTC", "reserve": "Резерв USDT"}
# public tickers (Binance futures / Hyperliquid names) → the OKX spot coin and how many coins one unit of the ticker is
RENAMED = {"1000BONK": ("BONK", 1000), "1000PEPE": ("PEPE", 1000), "1000SHIB": ("SHIB", 1000), "1000FLOKI": ("FLOKI", 1000),
           "1000SATS": ("SATS", 1000), "1000RATS": ("RATS", 1000), "1000LUNC": ("LUNC", 1000), "1000XEC": ("XEC", 1000),
           "1000CAT": ("CAT", 1000), "1000CHEEMS": ("CHEEMS", 1000), "1000WHY": ("WHY", 1000), "1MBABYDOGE": ("BABYDOGE", 1_000_000),
           "KPEPE": ("PEPE", 1000), "KSHIB": ("SHIB", 1000), "KBONK": ("BONK", 1000), "KFLOKI": ("FLOKI", 1000), "KLUNC": ("LUNC", 1000),
           "KDOGS": ("DOGS", 1000), "KNEIRO": ("NEIRO", 1000), "UBTC": ("BTC", 1), "UETH": ("ETH", 1), "USOL": ("SOL", 1),
           "UFART": ("FARTCOIN", 1), "UPUMP": ("PUMP", 1)}


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _p(v: float) -> str:
    return f"{round(v * 100, 2):g}".replace(".", ",") + "%"


def shares(env=None) -> dict:
    """{"spot", "core", "futures", "active", "reserve"} — shares of the whole account. A bad or missing env value → the default; if
    they add up to more than 100 % they are scaled down to 100 %."""
    e = os.environ if env is None else env
    w = {}
    for k, d in SHARES.items():
        v = _f((e.get(ENV_KEYS[k]) or "").strip())
        w[k] = v if v is not None and 0 <= v <= 1 else d
    t = sum(w.values())
    if t > 1:
        w = {k: v / t for k, v in w.items()}
    w["reserve"] = max(0.0, 1 - sum(w.values()))
    return {k: round(v, 6) for k, v in w.items()}


def core_extra(w: dict) -> dict:
    """{BTC / ETH / SOL: share of the WHOLE account} — the extra core (owner's decision of 28.10) in the core's plan proportions"""
    c, tot = float(w.get("core") or 0.0), sum(CORE_PLAN.values())
    return {k: c * v / tot for k, v in CORE_PLAN.items()} if c > 0 else {}


def fut_budget(share: float, capital: float, realized: float, upl: float, margin_open: float = 0.0) -> float:
    """Owner's decision of 28.10 (real money): the futures part = its share of the starting capital (the start + net deposits /
    withdrawals) + its own result (realized since the start, fees and funding included, + the open positions' P/L) — not a share
    of the account's current value; never below the margin of the open positions (a position is never squeezed)."""
    return round(max(float(margin_open or 0.0), float(share) * float(capital) + float(realized or 0.0) + float(upl or 0.0)), 2)


def split_text(w: dict, total: float | None = None) -> list[str]:
    out = []
    for k in ("spot", "core", "futures", "active", "reserve"):
        if w.get(k) or k != "reserve":
            out.append(f"{NAMES[k]}: {_p(w.get(k, 0))}" + (f" ≈ ${total * w.get(k, 0):,.0f}".replace(",", " ") if total else ""))
    return out


def fetch_portfolio(get=None) -> dict:
    """web/portfolio.json — the same public file the panel and the site read. A failure → {}."""
    def _get(name):
        req = urllib.request.Request(SRC + name + f"?t={int(time.time() // 120)}", headers={"User-Agent": "cnq-okx-autopilot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    try:
        return (get or _get)("portfolio.json") or {}
    except Exception:  # noqa: BLE001
        return {}


def own_reason(p: dict, actions: list) -> str:
    """Why the public portfolio holds this coin, in ITS own words (22.10): the reason of its latest buy / top-up of the current
    holding, else the position's own reason. Never the screener's today line (table_why_ru) — for a coin the portfolio already
    holds it can say the opposite («ждём просадку от −30%» next to a buy)."""
    sym, since = str(p.get("symbol") or "").upper(), int(p.get("entry_time") or 0)
    for a in sorted(actions or [], key=lambda a: -int(a.get("time") or 0)):
        if (str(a.get("symbol") or "").upper() == sym and a.get("action") in ("BUY", "ADD") and a.get("reason")
                and int(a.get("time") or 0) >= since - 86_400_000):
            return str(a["reason"]).strip()
    return str(p.get("reason") or "").strip()


def ideas(portfolio: dict) -> dict:
    """{ticker: row} for the open positions of «Наш портфель CRYPTO NOVA»; row["w"] = its weight in that portfolio."""
    pf = (portfolio.get("portfolios") or {}).get("ideas") or {}
    acts = [a for a in (pf.get("recent_actions") or []) + (pf.get("actions") or []) if isinstance(a, dict)]
    out = {}
    for p in pf.get("positions") or []:
        sym, w = str(p.get("symbol") or "").upper(), _f(p.get("weight"))
        if not sym or not w or w <= 0 or str(p.get("status") or "OPEN").upper() != "OPEN":
            continue
        lv = p.get("levels") or {}
        out[sym] = {"sym": sym, "w": w, "price": _f(p.get("price")), "name": p.get("name") or sym, "bucket": p.get("bucket"),
                    "kind_ru": p.get("kind_ru") or "", "role_ru": p.get("role_ru") or "", "why": own_reason(p, acts),
                    "stop": _f(lv.get("stop")), "take_half": _f(lv.get("take_half")), "target_w": _f(p.get("target_weight")),
                    "entry": _f(p.get("entry"))}
    return out


def meta(portfolio: dict) -> dict:
    pf = (portfolio.get("portfolios") or {}).get("ideas") or {}
    acts = [a for a in (pf.get("recent_actions") or []) + (pf.get("actions") or []) if isinstance(a, dict)]
    pos = ideas(portfolio)
    return {"asof": portfolio.get("asof"), "name": pf.get("name") or "CRYPTO NOVA портфель", "cash": max(0.0, 1 - sum(t["w"] for t in pos.values())),
            "last_action": max([int(a.get("time") or 0) for a in acts] or [0]), "actions": acts}


def reason(t: dict) -> str:
    """Why the public portfolio holds this coin — its own reason (own_reason) and its own stop, never the screener's advice."""
    head = t["name"] + (f" — {t['kind_ru']}" if t.get("kind_ru") else "")
    why = (t.get("why") or "").rstrip(" .")
    if why:
        why = "в портфеле CRYPTO NOVA: " + (why[0].lower() + why[1:] if why[1:2].islower() else why)
    elif t.get("role_ru"):
        why = "в портфеле CRYPTO NOVA: " + t["role_ru"]
    parts = [head] + ([why] if why else [])
    if t.get("core_extra"):
        parts.append(f"плюс доп. доля ядра {_p(t['core_extra'])} счёта (решение владельца 28.10: 10% из фьючерсов в ядро 30 / 10 / 5)")
    if t["sym"] in CORE:
        if "без стопа" not in why:
            parts.append("ядро — без стопа")
    elif t.get("stop"):
        parts.append(f"стоп портфеля {t['stop']:.6g}" + (f", на ×2 продаёт половину ({t['take_half']:.6g})" if t.get("take_half") else ""))
    return "; ".join(parts)


def sell_reason(sym: str, m: dict) -> str:
    """Why a held coin is not (or less) in the public portfolio: its last SELL / TRIM there, if any."""
    for a in sorted(m.get("actions") or [], key=lambda a: -int(a.get("time") or 0)):
        if str(a.get("symbol") or "").upper() == sym and a.get("action") in ("SELL", "TRIM") and a.get("reason"):
            return "портфель CRYPTO NOVA: " + str(a["reason"])
    return "этой монеты нет в портфеле CRYPTO NOVA"


def okx_base(sym: str) -> tuple[str, float]:
    """(OKX coin, coins per one unit of the ticker). 1000BONK → (BONK, 1000); an unknown 1000X → (X, 1000)."""
    s = sym.upper()
    if s in RENAMED:
        return RENAMED[s]
    m = re.fullmatch(r"1000([A-Z0-9]{2,})", s)
    return (m.group(1), 1000.0) if m else (s, 1.0)


def resolve(sym: str, ref_px: float | None, lookup) -> dict:
    """Map one public ticker to an OKX spot pair. lookup(coin) → (inst_id, last price) or None. No pair, no price to compare, or a
    price more than 25 % away → not mapped, with the reason. Never a guess."""
    base, mult = okx_base(sym)
    got = lookup(base)
    if not got:
        return {"ok": False, "sym": sym, "base": base, "why": f"на OKX нет спот-пары {base}"}
    inst, last = got
    if not ref_px or not last:
        return {"ok": False, "sym": sym, "base": base, "inst": inst, "why": f"нет цены, чтобы сверить {inst} с портфелем — не покупаю"}
    if abs(last * mult / ref_px - 1) > PRICE_TOL:
        return {"ok": False, "sym": sym, "base": base, "inst": inst,
                "why": f"цена {inst} ({last * mult:.6g} за {sym}) отличается от портфеля ({ref_px:.6g}) больше чем на 25% — возможно, другой токен"}
    return {"ok": True, "sym": sym, "base": base, "inst": inst, "mult": mult, "px": last}


def targets(w: dict, pos: dict) -> dict:
    """{ticker: share of the WHOLE account} = spot share × the coin's weight in the public portfolio (the extra core is added by
    okx_autopilot.build from core_extra — per OKX coin, after the ticker is mapped)."""
    return {s: w["spot"] * t["w"] for s, t in pos.items()}


def signature(portfolio: dict, w: dict) -> str:
    """Changes when the public portfolio changes (its coins, a new trade) or the split changes — not with prices."""
    pos = ideas(portfolio)
    return json.dumps({"w": {k: round(v, 4) for k, v in sorted(w.items())}, "c": sorted(pos), "a": meta(portfolio)["last_action"]},
                      sort_keys=True) if pos else ""

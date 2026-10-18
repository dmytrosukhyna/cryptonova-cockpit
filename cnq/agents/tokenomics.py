"""Доход держателям — which tokens get real money from their protocol (18.10.2026; sector research: the strongest tokens of
2025–26 — HYPE, SKY, UNI, TRX, PUMP — are the ones whose protocol spends revenue on buybacks / burns / payouts to holders; ONDO
and most «narrative» tokens get nothing). Owner: «недооценённые монеты учитывать в перспективу; перегретые не подбирать».

Source: DefiLlama's free API — holders revenue (the part of fees that goes to token holders: buybacks, burns, staking payouts)
for every protocol, 30 days and 1 year, joined to the token symbol through the protocols list (symbol / gecko_id). Output
web/tokenomics.json: {symbol: {name, holders_rev_30d_usd, holders_rev_1y_usd, slug}} + the top list. spot_ideas.py reads it:
a coin whose holders receive money gets a score bonus and a line in its «i». Missing data = no bonus (never a guess). Stdlib only."""
from __future__ import annotations

import json
import os
import time
import urllib.request

OVERVIEW = ("https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
            "&dataType=dailyHoldersRevenue")
PROTOCOLS = "https://api.llama.fi/protocols"


def _get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "cnq-tokenomics/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def build(overview: dict, protocols: list) -> dict:
    """Join holders revenue (per protocol or parent) to token symbols; the biggest protocol wins a shared symbol."""
    sym_of = {}
    for p in protocols or []:
        s = (p.get("symbol") or "").upper()
        if not s or s == "-":
            continue
        for k in ("slug", "name", "parentProtocol"):
            v = p.get(k)
            if v:
                sym_of.setdefault(str(v).lower(), s)
    out = {}
    for e in (overview or {}).get("protocols") or []:
        r30, r1y = e.get("total30d"), e.get("total1y")
        if not r30 and not r1y:
            continue
        s = None
        for k in ("slug", "name", "displayName", "parentProtocol", "defillamaId"):
            v = e.get(k)
            if v and str(v).lower() in sym_of:
                s = sym_of[str(v).lower()]
                break
        if not s:
            continue
        cur = out.get(s)
        if cur and (cur["holders_rev_30d_usd"] or 0) >= (r30 or 0):
            continue
        out[s] = {"name": e.get("displayName") or e.get("name"), "slug": e.get("slug"), "holders_rev_30d_usd": round(r30 or 0),
                  "holders_rev_1y_usd": round(r1y or 0)}
    return out


def run(out: str = "web", fetch=_get) -> dict:
    now = int(time.time() * 1000)
    err = None
    try:
        tok = build(fetch(OVERVIEW), fetch(PROTOCOLS))
    except Exception as e:  # noqa: BLE001
        tok, err = {}, f"{type(e).__name__}: {str(e)[:120]}"
    top = sorted(tok.items(), key=lambda kv: -kv[1]["holders_rev_30d_usd"])[:25]
    doc = {"asof": now, "error": err, "tokens": tok, "top": [{"symbol": s, **v} for s, v in top],
           "about_ru": "Доход держателям — часть комиссий протокола, которая идёт держателям токена: выкуп, сжигание или выплаты "
                       "стейкерам (DefiLlama, holders revenue). Токены, которым протокол реально платит, в 2025–2026 держались лучше "
                       "остальных. Нет данных — значит, держатели ничего не получают или DefiLlama это не считает."}
    if tok or not os.path.exists(os.path.join(out, "tokenomics.json")):    # a failed fetch keeps the last good file
        with open(os.path.join(out, "tokenomics.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    return doc

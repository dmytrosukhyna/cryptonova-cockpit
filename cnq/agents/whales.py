"""Whales department — what the best Hyperliquid accounts are doing, every run.

Source: Hyperliquid leaderboard (stats-data.hyperliquid.xyz) → top accounts by 30-day ROI with account value ≥ $1M and
positive all-time P&L → their open perp positions (clearinghouseState). We aggregate per coin: how many whales are long
vs short, net notional, average leverage; keep snapshots (whale_snapshots) and report what changed since the previous run
(opened / closed / flipped). Consensus = share of whale notional on one side.

Honesty note: whales are late-visible and often hedged; this is context for the panel ("киты лонгуют ETH 7 из 9"),
not a signal class until its hit rate is measured. We store every snapshot so that can be measured later.
Output: web/whales.json. Stdlib only.
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HL = "https://api.hyperliquid.xyz/info"
TOP_N = 20


def _get(url: str, data: dict | None = None, timeout: float = 30):
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                     headers={"Content-Type": "application/json", "User-Agent": "cryptonova-whales/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


def leaders(n: int = TOP_N) -> list[dict]:
    j = _get(LB) or {}
    rows = []
    for x in j.get("leaderboardRows", []) or []:
        m = {k: v for k, v in (x.get("windowPerformances") or [])}
        try:
            av = float(x.get("accountValue") or 0)
            roi = float((m.get("month") or {}).get("roi") or 0)
            pnl_m = float((m.get("month") or {}).get("pnl") or 0)
            alltime = float((m.get("allTime") or {}).get("pnl") or 0)
        except Exception:  # noqa: BLE001
            continue
        if av >= 1e6 and alltime > 0:
            rows.append({"addr": (x.get("ethAddress") or "").lower(), "name": x.get("displayName") or "", "value": av, "roi_m": roi, "pnl_m": pnl_m, "pnl_all": alltime})
    rows.sort(key=lambda r: -r["roi_m"])
    return rows[:n]


def positions(addr: str) -> list[dict]:
    st = _get(HL, {"type": "clearinghouseState", "user": addr}) or {}
    out = []
    for ap in st.get("assetPositions", []) or []:
        p = ap.get("position") or {}
        try:
            szi = float(p.get("szi") or 0)
            if szi == 0:
                continue
            out.append({"coin": p.get("coin"), "side": "LONG" if szi > 0 else "SHORT", "size": abs(szi), "notional": abs(float(p.get("positionValue") or 0)),
                        "entry": float(p.get("entryPx") or 0), "upnl": float(p.get("unrealizedPnl") or 0), "lev": float((p.get("leverage") or {}).get("value") or 0),
                        "liq": float(p.get("liquidationPx") or 0) if p.get("liquidationPx") else None})
        except Exception:  # noqa: BLE001
            continue
    return out


class Whales:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.execute("CREATE TABLE IF NOT EXISTS whale_snapshots(time INTEGER, addr TEXT, coin TEXT, side TEXT, notional REAL, entry REAL, lev REAL, PRIMARY KEY(time, addr, coin))")
        conn.execute("CREATE TABLE IF NOT EXISTS whale_meta(k TEXT PRIMARY KEY, v TEXT)")
        conn.commit()

    def last_time(self) -> int | None:
        r = self.conn.execute("SELECT MAX(time) FROM whale_snapshots").fetchone()
        return r[0] if r and r[0] else None

    def snapshot(self, t: int) -> dict[tuple[str, str], dict]:
        return {(a, c): {"side": s, "notional": n, "entry": e, "lev": l} for a, c, s, n, e, l in
                self.conn.execute("SELECT addr, coin, side, notional, entry, lev FROM whale_snapshots WHERE time=?", (t,)).fetchall()}

    def run(self, now: int) -> dict:
        lb = leaders()
        if not lb:
            return {"asof": now, "available": False, "error": "лидерборд Hyperliquid недоступен"}
        prev_t = self.last_time()
        prev = self.snapshot(prev_t) if prev_t else {}
        names = {}
        try:
            names = json.loads((self.conn.execute("SELECT v FROM whale_meta WHERE k='names'").fetchone() or ["{}"])[0])
        except Exception:  # noqa: BLE001
            names = {}
        cur: dict[tuple[str, str], dict] = {}
        whales = []
        for w in lb:
            pos = positions(w["addr"])
            time.sleep(0.4)
            names[w["addr"]] = w["name"] or names.get(w["addr"]) or f"лидер +{w['roi_m']*100:.0f}%"
            for p in pos:
                cur[(w["addr"], p["coin"])] = {"side": p["side"], "notional": p["notional"], "entry": p["entry"], "lev": p["lev"]}
                self.conn.execute("INSERT OR REPLACE INTO whale_snapshots VALUES(?,?,?,?,?,?,?)", (now, w["addr"], p["coin"], p["side"], p["notional"], p["entry"], p["lev"]))
            whales.append({**w, "name": names[w["addr"]], "positions": sorted(pos, key=lambda p: -p["notional"])[:8], "n_pos": len(pos)})
        self.conn.execute("INSERT OR REPLACE INTO whale_meta VALUES('names',?)", (json.dumps(names, ensure_ascii=False),))
        self.conn.commit()
        # aggregate per coin
        agg: dict[str, dict] = {}
        for (addr, coin), p in cur.items():
            a = agg.setdefault(coin, {"coin": coin, "long_n": 0, "short_n": 0, "long_usd": 0.0, "short_usd": 0.0, "levs": [], "entries_long": [], "entries_short": []})
            if p["side"] == "LONG":
                a["long_n"] += 1; a["long_usd"] += p["notional"]; a["entries_long"].append(p["entry"])
            else:
                a["short_n"] += 1; a["short_usd"] += p["notional"]; a["entries_short"].append(p["entry"])
            if p["lev"]:
                a["levs"].append(p["lev"])
        coins = []
        for a in agg.values():
            tot = a["long_usd"] + a["short_usd"]
            cons = (a["long_usd"] - a["short_usd"]) / tot if tot else 0
            coins.append({"coin": a["coin"], "long_n": a["long_n"], "short_n": a["short_n"], "long_usd": round(a["long_usd"]), "short_usd": round(a["short_usd"]),
                          "consensus": round(cons, 2), "lean": "LONG" if cons > 0.3 else "SHORT" if cons < -0.3 else "MIXED",
                          "avg_lev": round(sum(a["levs"]) / len(a["levs"]), 1) if a["levs"] else None,
                          "avg_entry_long": round(sum(a["entries_long"]) / len(a["entries_long"]), 6) if a["entries_long"] else None,
                          "avg_entry_short": round(sum(a["entries_short"]) / len(a["entries_short"]), 6) if a["entries_short"] else None})
        coins.sort(key=lambda c: -(c["long_usd"] + c["short_usd"]))
        # changes vs previous snapshot (only among the same whales)
        changes = []
        for key, p in cur.items():
            q = prev.get(key)
            if not q:
                if prev:
                    changes.append({"addr": key[0], "name": names.get(key[0], ""), "coin": key[1], "kind": "OPEN", "side": p["side"], "notional": round(p["notional"]), "entry": p["entry"]})
            elif q["side"] != p["side"]:
                changes.append({"addr": key[0], "name": names.get(key[0], ""), "coin": key[1], "kind": "FLIP", "side": p["side"], "notional": round(p["notional"]), "entry": p["entry"]})
            elif p["notional"] > q["notional"] * 1.5:
                changes.append({"addr": key[0], "name": names.get(key[0], ""), "coin": key[1], "kind": "ADD", "side": p["side"], "notional": round(p["notional"]), "entry": p["entry"]})
        for key, q in prev.items():
            if key not in cur and key[0] in {w["addr"] for w in lb}:
                changes.append({"addr": key[0], "name": names.get(key[0], ""), "coin": key[1], "kind": "CLOSE", "side": q["side"], "notional": round(q["notional"]), "entry": q["entry"]})
        changes.sort(key=lambda c: -c["notional"])
        total_long = sum(c["long_usd"] for c in coins); total_short = sum(c["short_usd"] for c in coins)
        return {"asof": now, "available": True, "prev_time": prev_t, "whales": whales, "coins": coins[:30], "changes": changes[:25],
                "summary": {"n_whales": len(whales), "total_long_usd": round(total_long), "total_short_usd": round(total_short),
                            "bias": "LONG" if total_long > total_short * 1.3 else "SHORT" if total_short > total_long * 1.3 else "MIXED"},
                "note": "Топ-20 счетов лидерборда Hyperliquid по доходности за 30 дней (≥ $1M, плюс за всё время). Это контекст, не сигнал: "
                        "киты часто хеджируют и видны с опозданием. Снимки копим, чтобы измерить, предсказывает ли их позиция движение."}


def run(conn: sqlite3.Connection, now: int | None = None) -> dict:
    return Whales(conn).run(now or int(time.time() * 1000))

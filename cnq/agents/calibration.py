"""Forecast calibration department — records the panel's A/B forecast for every coin on the server, resolves it
against what actually happened, and reports hit rate and Brier score so the panel can show an honest number.

The forecast mirrors the panel's forecast(): p_up = 0.5 + regime terms + BTC regime + market stress + news; the browser
adds order-book terms the server cannot see (noted in `components`). Scenario A = "first move" to the nearest zone:
UP means the resistance edge is touched before the support edge within the horizon (24 h); if neither is touched,
the 24 h close decides. Deterministic, stdlib only, no look-ahead (only candles closed after the forecast resolve it).

Tables (data/agents.sqlite): forecasts. Output: web/calibration.json.
"""
from __future__ import annotations

import json
import sqlite3
import time

HORIZON_MS = 24 * 3600_000
BUCKET_MS = 4 * 3600_000
MID = {"LONG": .12, "UP_WAIT": .06, "DOWN_WAIT": -.06, "SHORT": -.12}
SHORT = {"UP": .05, "DOWN": -.05, "FLAT": 0.0}


def _atr(c15: list[list], n: int = 14) -> float:
    if len(c15) < n + 2:
        return 0.0
    trs = [max(c15[i][2] - c15[i][3], abs(c15[i][2] - c15[i - 1][4]), abs(c15[i][3] - c15[i - 1][4])) for i in range(len(c15) - n, len(c15))]
    return sum(trs) / n


def stress_level(coins: list[dict]) -> int:
    xs = []
    for c in coins:
        cs = c.get("candles") or []
        if len(cs) > 97:
            xs.append(cs[-1][4] / cs[-97][4] - 1)
    if not xs:
        return 0
    d = len([x for x in xs if x < -0.03]) / len(xs)
    return 3 if d >= 0.9 else 2 if d >= 0.7 else 1 if d >= 0.4 else 0


def p_up(coin: dict, btc_bull: bool, stress: int, news: list[dict], now: int) -> tuple[float, dict]:
    r = coin.get("regime") or {}
    comp = {"mid": MID.get(r.get("mid"), 0.0), "short": SHORT.get(r.get("short"), 0.0), "long": .03 if r.get("long") == "BULL" else -.03}
    base = coin["symbol"].replace("-USDT", "")
    comp["btc"] = 0.0 if base == "BTC" else (.03 if btc_bull else -.05)
    # stress pulls every coin down, but a coin that already dumped inside the sell-off tends to be bought over the next
    # day (docs/research/exhaustion_long.md) — so the stress term is halved and a mean-reversion term is added per coin
    comp["stress"] = -0.04 * stress
    cs = coin.get("candles") or []
    r24 = cs[-1][4] / cs[-97][4] - 1 if len(cs) > 97 else 0.0
    comp["mr"] = (0.06 if r24 <= -0.05 else 0.03 if r24 <= -0.03 else 0.0) if stress >= 2 else 0.0
    # the coin's own 7-day momentum (coin-specific, not BTC-driven)
    comp["mom7"] = 0.03 if (r.get("r7") or 0) > 0.03 else -0.03 if (r.get("r7") or 0) < -0.03 else 0.0
    # news: each story counts once (not once per run/asset row); market-wide only for macro / regulation / ETF flows;
    # capped at ±0.06 — on 15.10 nine duplicated bearish rows (−0.27) pinned every forecast at 22 % and the hit rate fell to 39 %
    seen: dict[str, int] = {}
    for i in news:
        if i.get("magnitude", 0) < 4 or now - (i.get("created") or 0) > 36 * 3600_000:
            continue
        a = i.get("asset")
        if not (a == base or (a == "MARKET" and i.get("category") in ("macro", "regulation", "etf_flows"))):
            continue
        d = 1 if i.get("direction") == "bull" else -1 if i.get("direction") == "bear" else 0
        if d:
            seen[str(i.get("news_id") or i.get("title"))] = d
    comp["news"] = max(-0.06, min(0.06, 0.03 * sum(seen.values())))
    p = 0.5 + sum(comp.values())
    return max(.22, min(.78, p)), comp


def targets(coin: dict) -> tuple[float | None, float | None]:
    price = coin["price"]
    lv = [l for l in coin.get("levels", []) if l.get("status") in (None, "ACTIVE")]
    sup = sorted([l for l in lv if l["center"] < price], key=lambda l: -l["center"])
    res = sorted([l for l in lv if l["center"] > price], key=lambda l: l["center"])
    a = _atr(coin.get("candles") or []) or price * 0.01
    up = res[0]["lo"] if res else price + 2.5 * a
    dn = sup[0]["hi"] if sup else price - 2.5 * a
    up = min(up, price + 4 * a); dn = max(dn, price - 4 * a)
    return up, dn


class Calibration:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.executescript("""CREATE TABLE IF NOT EXISTS forecasts(id TEXT PRIMARY KEY, time INTEGER, symbol TEXT, p_up REAL, price REAL,
            up_target REAL, dn_target REAL, horizon INTEGER, outcome TEXT, resolved INTEGER, hit_price REAL, components TEXT);""")
        conn.commit()

    def record(self, coins: list[dict], btc_bull: bool, news: list[dict], now: int) -> int:
        stress = stress_level(coins)
        n = 0
        for c in coins:
            if not c.get("candles") or not c.get("price"):
                continue
            fid = f"{c['symbol']}-{now // BUCKET_MS}"
            if self.conn.execute("SELECT 1 FROM forecasts WHERE id=?", (fid,)).fetchone():
                continue
            p, comp = p_up(c, btc_bull, stress, news, now)
            up, dn = targets(c)
            self.conn.execute("INSERT INTO forecasts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (fid, now, c["symbol"], p, c["price"], up, dn, HORIZON_MS, None, None, None, json.dumps(comp)))
            n += 1
        self.conn.commit()
        return n

    def resolve(self, coins: list[dict], now: int) -> int:
        by = {c["symbol"]: c for c in coins}
        n = 0
        for fid, t, sym, price, up, dn, hz in self.conn.execute("SELECT id,time,symbol,price,up_target,dn_target,horizon FROM forecasts WHERE outcome IS NULL").fetchall():
            c = by.get(sym)
            if not c:
                continue
            outcome, hit, rt = None, None, None
            last_close = None
            for k in c["candles"]:
                if k[0] < t or k[0] + 900_000 > now:
                    continue
                if k[0] > t + hz:
                    break
                last_close = (k[0], k[4])
                hi_hit, lo_hit = k[2] >= up, k[3] <= dn
                if hi_hit and lo_hit:   # both edges inside one candle: decide by the close
                    outcome = "UP" if k[4] >= price else "DOWN"; hit = k[4]; rt = k[0]; break
                if hi_hit:
                    outcome, hit, rt = "UP", up, k[0]; break
                if lo_hit:
                    outcome, hit, rt = "DOWN", dn, k[0]; break
            if outcome is None and last_close and last_close[0] >= t + hz - 900_000:
                outcome = "UP" if last_close[1] >= price else "DOWN"; hit = last_close[1]; rt = last_close[0]
            if outcome:
                self.conn.execute("UPDATE forecasts SET outcome=?, resolved=?, hit_price=? WHERE id=?", (outcome, rt, hit, fid))
                n += 1
        self.conn.commit()
        return n

    def stats(self) -> dict:
        rows = self.conn.execute("SELECT symbol, p_up, outcome, time FROM forecasts WHERE outcome IS NOT NULL").fetchall()
        n = len(rows)
        if not n:
            return {"n": 0, "pending": self.conn.execute("SELECT count(*) FROM forecasts WHERE outcome IS NULL").fetchone()[0]}
        brier = sum((p - (1.0 if o == "UP" else 0.0)) ** 2 for _, p, o, _ in rows) / n
        hits = sum(1 for _, p, o, _ in rows if (p >= 0.5) == (o == "UP"))
        conf = [(p, o) for _, p, o, _ in rows if abs(p - 0.5) >= 0.15]
        conf_hits = sum(1 for p, o in conf if (p >= 0.5) == (o == "UP"))
        bins = []
        for lo, hi in ((0.2, 0.35), (0.35, 0.5), (0.5, 0.65), (0.65, 0.8)):
            b = [(p, o) for _, p, o, _ in rows if lo <= p < hi]
            if b:
                bins.append({"range": f"{int(lo*100)}–{int(hi*100)}%", "n": len(b), "forecast_up": round(sum(p for p, _ in b) / len(b), 3),
                             "actual_up": round(sum(1 for _, o in b if o == "UP") / len(b), 3)})
        per = {}
        for s, p, o, _ in rows:
            d = per.setdefault(s.replace("-USDT", ""), {"n": 0, "hits": 0})
            d["n"] += 1; d["hits"] += 1 if (p >= 0.5) == (o == "UP") else 0
        base_up = sum(1 for _, _, o, _ in rows if o == "UP") / n
        return {"n": n, "pending": self.conn.execute("SELECT count(*) FROM forecasts WHERE outcome IS NULL").fetchone()[0],
                "hit_rate": round(hits / n, 3), "confident_n": len(conf), "confident_hit_rate": round(conf_hits / len(conf), 3) if conf else None,
                "brier": round(brier, 4), "brier_coin_flip": 0.25, "base_rate_up": round(base_up, 3), "bins": bins,
                "per_coin": {k: {"n": v["n"], "hit_rate": round(v["hits"] / v["n"], 2)} for k, v in sorted(per.items())},
                "since": min(t for *_, t in rows)}


def run(conn: sqlite3.Connection, report: dict, news: list[dict] | None = None, now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    btc = next((c for c in report["coins"] if c["symbol"].startswith("BTC")), None)
    btc_bull = bool(btc and (btc.get("regime") or {}).get("long") == "BULL")
    cal = Calibration(conn)
    resolved = cal.resolve(report["coins"], now)
    recorded = cal.record(report["coins"], btc_bull, news or [], now)
    st = cal.stats()
    cur = {r[0].replace("-USDT", ""): {"p_up": round(r[1], 3), "up": r[2], "dn": r[3], "time": r[4]} for r in
           conn.execute("SELECT symbol, p_up, up_target, dn_target, time FROM forecasts WHERE outcome IS NULL ORDER BY time")}
    return {"asof": now, "recorded": recorded, "resolved": resolved, "stats": st, "open": cur,
            "note": "Прогноз A/B записывается на сервере каждые 4 часа и проверяется через сутки: сначала к сопротивлению или к поддержке. "
                    "Попадание = сценарий A сбылся. Brier 0.25 = монетка, меньше — лучше."}

"""«Умное усреднение» в спот-портфеле: стоит ли боту докупать монету на просадке, если перспективы есть (20.10.2026).

Owner: «если NEAR сегодня −4.2% в нашем спот-портфеле, разумно ли боту усреднить — докупить немного, если мы видим перспективы?»

The model is the «Умеренный» spot portfolio (cnq/agents/portfolio.py → SpotPortfolio.step) replayed day by day on daily closes,
as one compounding account of $10 000 (weights = shares of the account's current value):
  core     BTC 35 % / ETH 15 % (BTC «bear»: 25 / 10); missing core coin → bought at once; every Monday a core coin is brought back to
           its target when it is more than 2 p.p. off (BUY / ADD / TRIM)
  mid      up to 5 × 8 % — on Mondays, the best «mid» scores ≥ 60 (no memes, no coin after +40 % in 7 days / +120 % in 30),
           while invested + 8 % ≤ 90 %
  short    up to 3 × 5 % — on Mondays in BTC «bull» only, «short» scores ≥ 70
  exits    (satellites, every day, in this order) −15 % from the average entry → sell; short: score < 40 or BTC not «bull» → sell;
           +40 % from entry and position cost > 3 % of the account → sell half; mid: Monday and score < 35 → sell;
           25 % below the position high while +20 % from entry → sell
  BTC «bull» = BTC close above its 200-day average and up over 90 days (cnq/terminal/report.regime «long» = BULL).
Scores = portfolio.score_universe on a point-in-time universe: each day the 150 most traded coins (30-day average quote volume
≥ $2M, ≥ 30 days of history); mid pool = the 100 most traded with ≥ 180 days; ranks of 7 / 30 / 200-day change as in the code.
What history cannot give is set neutral: volume/market cap rank = 0.5 for every coin, «on Hyperliquid» = +4 for every coin,
no owner trust list, no funding penalty. «Blown» = more than 85 % below the highest close since the data starts.

Costs: 0.1 % fee per trade + slippage 0.05 % (BTC, ETH, SOL) / 0.15 % (other coins) per side. Signals and fills on the daily
close (the live bot acts on the current price when a rule fires). Idle cash earns nothing. A delisted coin is sold at its last
close. Stops are checked on daily closes, not intraday.

Variants (fixed BEFORE the run; the only parameter, the dip size 10 % / 20 %, is picked per family on 2023–2024 only):
  A        current rules (baseline)
  B        the core drift band (2 p.p.) checked every day instead of Mondays
  C10/C20  top-up on a dip: a core or mid coin whose close is ≥ 10 % (C20: ≥ 20 %) below its average entry and whose
           «prospects» still hold — mid score ≥ 60 OR close above the coin's own 200-day average — gets + 1/3 of its target weight
           (core 35/15 or 25/10 %, mid 8 %) from cash; at most 2 top-ups per coin in any 30 days; never above 1.5 × target;
           cash stays ≥ 10 %. The average entry moves down like the live code's harmonic entry (so the −15 % stop and +40 %
           take-half are measured from it). For a core coin the top-up also raises its Monday target by the same amount until the
           close is back at the average entry (or the BTC regime flips) — otherwise the next Monday would sell it straight back.
           Order of a day: exits → top-ups → Monday fills.
  D10/D20  the same, only BTC, ETH, SOL (SOL only when the portfolio holds it as a mid pick)
  E10/E20  control: C without the «prospects» filter
Pass (all, against A, on the main data set = Binance spot, every USDT pair incl. delisted):
  1. higher total return over the whole period;
  2. worst drawdown not deeper than A's by more than 2 p.p.;
  3. better than A in at least 3 of 4 calendar years (2023, 2024, 2025, 2026 to date);
  4. better than A on the test years 2025-01 → 2026-10 (the dip size was picked on 2023–2024);
  5. not 1–2 trades: the extra profit over A minus the profit of the 2 best top-ups is still > 0;
  and on the second data set (Binance USDT-M perpetuals listed by 2023-01, delisted incl., from 2023-06) not worse than A in total.

Usage:
  python scripts/spot_averaging_test.py spot1d.npz [--name spot] [--md out.md] [--json out.json]
  python scripts/spot_averaging_test.py alt100_all2023.npz --from15m --name perps      (the lab's wide 15-minute set)
  python scripts/spot_averaging_test.py --annotate out.md                               (GitHub annotations, ≤ 4 KB each)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

import numpy as np

DAY = 86_400_000
FEE = 0.001
SLIP_MAJOR, SLIP_ALT = 0.0005, 0.0015
MAJORS = {"BTC", "ETH", "SOL"}
CORE_BULL = {"BTC": 0.35, "ETH": 0.15}
CORE_BEAR = {"BTC": 0.25, "ETH": 0.10}
MID_W, MID_N, SHORT_W, SHORT_N = 0.08, 5, 0.05, 3
INVEST_CAP, MIN_CASH = 0.90, 0.10
UNIV_N, MID_POOL_RANK, MIN_VOL = 150, 100, 2e6
START_CAP = 10_000.0
TRAIN_END = "2024-12-31"
STABLE = set("""USDT USDC DAI FDUSD TUSD USDE USDS PYUSD USD1 BUSD USDD FRAX LUSD GUSD USDP EURC USDL RLUSD PAX UST USTC AEUR EUR
EURI GBP AUD BRL TRY XUSD BFUSD VAI SUSD""".split())
WRAPPED = set("""WBTC WETH STETH WSTETH WEETH CBBTC RETH WBETH BETH LBTC SOLVBTC TBTC CBETH PAXG XAUT BTCB BNSOL BTCST""".split())
MEMES = set("""DOGE PEPE BONK SHIB FLOKI WIF POPCAT MEW BOME BRETT MOODENG PNUT GOAT CHILLGUY FARTCOIN SPX PENGU TRUMP MELANIA
MYRO NEIRO NEIROETH TURBO MOG PEOPLE TST ACT MEME DOGS GRIFFAIN ZEREBRO AI16Z VINE YZY GIGA PONKE SLERF WEN TOSHI DEGEN MOTHER
USELESS SATS BABYDOGE CAT SUNDOG MAGA BODEN NOT PUMP LADYS CHEEMS MUBARAK BROCCOLI714 BANANAS31 PUFFER KOMA WHY DOGINME""".split())


def is_meme(s: str) -> bool:
    return s in MEMES or re.sub(r"^(1000000|1000|1M|K)", "", s) in MEMES


def ymd(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------------------------------------------- data
def load(path: str, from15m: bool) -> dict:
    Z = np.load(path, allow_pickle=True)
    syms = [str(s) for s in Z["syms"]]
    if not from15m:
        return {"syms": syms, "days": Z["days"].astype(np.int64), "close": Z["close"].astype(float), "qvol": Z["qvol"].astype(float)}
    times = Z["times"]
    end_mask = (times % DAY) == DAY - 900_000
    days = times[end_mask] // DAY * DAY
    close = Z["close"][:, end_mask].astype(float)
    nd = len(days)
    di = np.clip(np.searchsorted(days, times // DAY * DAY), 0, nd - 1)
    qv = np.zeros((len(syms), nd))
    for i in range(len(syms)):
        np.add.at(qv[i], di, np.nan_to_num(Z["volume_quote"][i].astype(float)))
    qv[~np.isfinite(close)] = np.nan
    return {"syms": syms, "days": days.astype(np.int64), "close": close, "qvol": qv}


def features(D: dict) -> dict:
    syms, days, C = D["syms"], D["days"], D["close"]
    C = np.where(C > 0, C, np.nan)
    n, nd = C.shape
    valid = np.isfinite(C)
    first = np.where(valid.any(1), valid.argmax(1), nd)
    last = np.where(valid.any(1), nd - 1 - valid[:, ::-1].argmax(1), -1)
    Cf = C.copy()                                    # forward-filled close (valuation inside a pair's life)
    for j in range(1, nd):
        m = ~np.isfinite(Cf[:, j])
        Cf[m, j] = Cf[m, j - 1]
    q = np.nan_to_num(D["qvol"])
    cs = np.cumsum(q, 1)
    vol30 = (cs - np.concatenate([np.zeros((n, 30)), cs[:, :-30]], 1)) / 30
    csum = np.cumsum(np.where(np.isfinite(Cf), Cf, 0.0), 1)
    sma200 = np.full_like(Cf, np.nan)
    sma200[:, 199:] = (csum[:, 199:] - np.concatenate([np.zeros((n, 1)), csum[:, :-200]], 1)) / 200
    idx = np.arange(nd)[None, :]
    sma200[(idx - first[:, None]) < 199] = np.nan

    def chg(k):
        out = np.full_like(Cf, np.nan)
        out[:, k:] = Cf[:, k:] / Cf[:, :-k] - 1
        out[(idx - first[:, None]) < k] = np.nan
        return out
    ath = np.fmax.accumulate(np.where(np.isfinite(Cf), Cf, -np.inf), axis=1)
    bi = syms.index("BTC")
    b = Cf[bi]
    r90 = np.full(nd, np.nan)
    r90[90:] = b[90:] / b[:-90] - 1
    bull = (b > sma200[bi]) & (r90 > 0)
    excl = np.array([s in STABLE or s in WRAPPED for s in syms])
    return {"syms": syms, "days": days, "C": C, "Cf": Cf, "valid": valid, "first": first, "last": last, "vol30": vol30,
            "sma200": sma200, "chg7": chg(7), "chg30": chg(30), "chg200": chg(200), "ath": ath, "bull": bull, "excl": excl,
            "meme": np.array([is_meme(s) for s in syms])}


def rank_pct(v: np.ndarray) -> np.ndarray:
    """portfolio._rank_pct: ascending rank / (n − 1) among non-missing values (stable order), missing → NaN."""
    out = np.full(len(v), np.nan)
    ok = np.where(np.isfinite(v))[0]
    if len(ok):
        order = ok[np.argsort(v[ok], kind="stable")]
        out[order] = np.arange(len(order)) / max(1, len(order) - 1)
    return out


def day_scores(F: dict, j: int) -> dict:
    """Universe of day j (indices in vol order) and the code's mid / short scores for it."""
    v = F["vol30"][:, j]
    age = j - F["first"]
    ok = F["valid"][:, j] & ~F["excl"] & (age >= 30) & (v >= MIN_VOL)
    cand = np.where(ok)[0]
    U = cand[np.argsort(-v[cand], kind="stable")][:UNIV_N]
    c7, c30, c200 = F["chg7"][U, j], F["chg30"][U, j], F["chg200"][U, j]
    p7, p30, p200 = (np.nan_to_num(rank_pct(x)) for x in (c7, c30, c200))
    bull = bool(F["bull"][j])
    dd = np.where(F["ath"][U, j] > 0, F["Cf"][U, j] / F["ath"][U, j] - 1, -0.5)
    s_short = 100 * (0.45 * p7 + 0.35 * p30 + 0.2 * 0.5)
    if not bull:
        s_short *= 0.5
    s_mid = 100 * (0.4 * p30 + 0.4 * p200 + 0.2 * 0.5) + 4
    s_mid -= 25 * ((np.nan_to_num(c30) < 0) & (np.nan_to_num(c200) < 0))
    s_mid -= 20 * (dd < -0.85)
    s_short, s_mid = np.clip(np.round(s_short), 0, 100), np.clip(np.round(s_mid), 0, 100)
    hot = (np.nan_to_num(c7) >= 0.40) | (np.nan_to_num(c30) >= 1.20)
    rank = np.arange(1, len(U) + 1)
    return {"U": U, "mid": dict(zip(U.tolist(), s_mid.tolist())), "short": dict(zip(U.tolist(), s_short.tolist())),
            "hot": dict(zip(U.tolist(), hot.tolist())), "rank": dict(zip(U.tolist(), rank.tolist())), "age": age}


# ------------------------------------------------------------------------------------------------------------ simulation
class Book:
    def __init__(self, F: dict):
        self.F = F
        self.cash = START_CAP
        self.pos: dict[int, dict] = {}
        self.lots: list[dict] = []
        self.trades = 0
        self.costs = 0.0

    def slip(self, i: int) -> float:
        return SLIP_MAJOR if self.F["syms"][i] in MAJORS else SLIP_ALT

    def px(self, i: int, j: int) -> float:
        return float(self.F["Cf"][i, j])

    def value(self, j: int) -> float:
        return self.cash + sum(p["u"] * self.px(i, j) for i, p in self.pos.items())

    def mw(self, i: int, j: int, V: float) -> float:
        p = self.pos.get(i)
        return p["u"] * self.px(i, j) / V if p and V > 0 else 0.0

    def buy(self, i: int, usd: float, j: int, bucket: str, tag: str) -> float:
        usd = min(usd, self.cash / (1 + FEE))
        if usd <= 1e-6:
            return 0.0
        px = self.px(i, j)
        s = self.slip(i)
        u = usd / (px * (1 + s))
        self.cash -= usd * (1 + FEE)
        self.costs += usd * FEE + u * px * s
        p = self.pos.get(i)
        if p is None:
            p = self.pos[i] = {"u": 0.0, "cost_raw": 0.0, "bucket": bucket, "high": px, "lots": [], "tops": [], "extra": 0.0, "since": j}
        p["u"] += u
        p["cost_raw"] += u * px                     # the live code's harmonic entry is on raw prices
        lot = {"sym": self.F["syms"][i], "u": u, "px": usd * (1 + FEE) / u, "tag": tag, "day": j, "pnl": 0.0, "usd": usd}
        p["lots"].append(lot)
        self.lots.append(lot)
        self.trades += 1
        return usd

    def sell(self, i: int, frac: float, j: int) -> None:
        p = self.pos[i]
        frac = min(1.0, max(0.0, frac))
        if frac <= 0:
            return
        px = self.px(i, j)
        s = self.slip(i)
        u = p["u"] * frac
        q = px * (1 - s) * (1 - FEE)
        self.cash += u * q
        self.costs += u * px * (s + (1 - s) * FEE)
        for lot in p["lots"]:
            lu = lot["u"] * frac
            lot["pnl"] += lu * (q - lot["px"])
            lot["u"] -= lu
        self.trades += 1
        if frac >= 1 - 1e-9:
            del self.pos[i]
        else:
            p["u"] -= u
            p["cost_raw"] *= 1 - frac

    def entry(self, i: int) -> float:
        p = self.pos[i]
        return p["cost_raw"] / p["u"]

    def to_target(self, i: int, T: float, j: int, V: float, bucket: str, tag: str) -> None:
        cur = self.mw(i, j, V)
        if T > cur:
            self.buy(i, (T - cur) * V, j, bucket, tag)
        elif T < cur and i in self.pos:
            self.sell(i, 1 - T / cur, j)


def simulate(F: dict, j0: int, j1: int, variant: str, cache: dict) -> dict:
    fam = variant[0]
    X = int(variant[1:]) / 100 if len(variant) > 1 else None
    syms, days = F["syms"], F["days"]
    sidx = {s: k for k, s in enumerate(syms)}
    core_ids = {sidx[s]: s for s in CORE_BULL}
    d_ids = {sidx[s] for s in ("BTC", "ETH", "SOL") if s in sidx}
    bk = Book(F)
    eq, n_top, blocked = [], 0, 0
    prev_bull = None
    for j in range(j0, j1 + 1):
        if j not in cache:
            cache[j] = day_scores(F, j)
        S = cache[j]
        bull = bool(F["bull"][j])
        monday = dt.datetime.fromtimestamp(days[j] / 1000, dt.timezone.utc).weekday() == 0
        # 0. delisted / halted for good → sold at the last close
        for i in [i for i in bk.pos if F["last"][i] < j]:
            bk.sell(i, 1.0, F["last"][i] if F["last"][i] >= 0 else j)
        rebalance = monday or not bk.pos
        T_core = CORE_BULL if bull else CORE_BEAR
        if prev_bull is not None and bull != prev_bull:
            for p in bk.pos.values():
                p["extra"] = 0.0
        prev_bull = bull
        V = bk.value(j)
        # 1. core
        for i, s in core_ids.items():
            if not F["valid"][i, j] and i not in bk.pos:
                continue
            T = T_core[s]
            p = bk.pos.get(i)
            if p is None:
                bk.buy(i, T * V, j, "core", "base")
                continue
            if p["extra"] and bk.px(i, j) >= bk.entry(i):
                p["extra"] = 0.0
            Te = min(1.5 * T, T + p["extra"])
            if (rebalance or fam == "B") and abs(bk.mw(i, j, V) - Te) > 0.02:
                bk.to_target(i, Te, j, V, "core", "rebalance")
        # 2. satellites: highs, exits (the code's elif chain)
        for i in list(bk.pos):
            p = bk.pos[i]
            if p["bucket"] == "core":
                continue
            px = bk.px(i, j)
            p["high"] = max(p["high"], px)
            ret = px / bk.entry(i) - 1
            sc = (S["mid"] if p["bucket"] == "mid" else S["short"]).get(i, 0)
            V = bk.value(j)
            if ret <= -0.15:
                bk.sell(i, 1.0, j)
            elif p["bucket"] == "short" and (sc < 40 or not bull):
                bk.sell(i, 1.0, j)
            elif ret >= 0.40 and p["cost_raw"] / V > 0.03:          # «weight > 3 %»: cost of the position
                bk.sell(i, 0.5, j)
            elif p["bucket"] == "mid" and sc < 35 and rebalance:
                bk.sell(i, 1.0, j)
            elif px < p["high"] * 0.75 and ret > 0.2:
                bk.sell(i, 1.0, j)
        # 3. top-ups (C / D / E)
        if fam in "CDE":
            for i in list(bk.pos):
                p = bk.pos[i]
                if p["bucket"] == "short" or (fam == "D" and i not in d_ids):
                    continue
                px = bk.px(i, j)
                if px > (1 - X) * bk.entry(i):
                    continue
                if len([t for t in p["tops"] if j - t < 30]) >= 2:
                    continue
                if fam != "E":
                    sma = F["sma200"][i, j]
                    if not (S["mid"].get(i, 0) >= 60 or (np.isfinite(sma) and px > sma)):
                        blocked += 1
                        continue
                V = bk.value(j)
                T = T_core[core_ids[i]] if p["bucket"] == "core" else MID_W
                room = 1.5 * T - bk.mw(i, j, V)
                add = min(T / 3, room) * V
                add = min(add, (bk.cash - MIN_CASH * V) / (1 + FEE))
                if add < 0.005 * V:
                    continue
                got = bk.buy(i, add, j, p["bucket"], "topup")
                if got > 0:
                    p["tops"].append(j)
                    n_top += 1
                    if p["bucket"] == "core":
                        p["extra"] += got / V
        # 4. Monday fills
        if rebalance:
            V = bk.value(j)
            invested = sum(bk.mw(i, j, V) for i in bk.pos)
            held = set(bk.pos)
            pool = [i for i in S["U"].tolist() if syms[i] not in CORE_BULL and not F["meme"][i] and not S["hot"][i]
                    and S["rank"][i] <= MID_POOL_RANK and S["age"][i] >= 180]
            pool.sort(key=lambda i: -S["mid"][i])
            mids = [i for i in pool[:8] if S["mid"][i] >= 60 and i not in held]
            n_mid = sum(1 for p in bk.pos.values() if p["bucket"] == "mid")
            for i in mids[: max(0, MID_N - n_mid)]:
                if invested + MID_W > INVEST_CAP:
                    break
                bk.buy(i, MID_W * V, j, "mid", "base")
                invested += MID_W
                held.add(i)
            if bull:
                pool = [i for i in S["U"].tolist() if syms[i] not in CORE_BULL and not F["meme"][i] and not S["hot"][i]]
                pool.sort(key=lambda i: -S["short"][i])
                shorts = [i for i in pool[:8] if S["short"][i] >= 70 and i not in held]
                n_s = sum(1 for p in bk.pos.values() if p["bucket"] == "short")
                for i in shorts[: max(0, SHORT_N - n_s)]:
                    if invested + SHORT_W > INVEST_CAP:
                        break
                    bk.buy(i, SHORT_W * V, j, "short", "base")
                    invested += SHORT_W
                    held.add(i)
        eq.append(bk.value(j))
    # open lots marked at the last close (no exit costs, same as the equity curve)
    for i, p in bk.pos.items():
        for lot in p["lots"]:
            lot["pnl"] += lot["u"] * (bk.px(i, j1) - lot["px"])
            lot["u"] = 0.0
    tops = sorted([l for l in bk.lots if l["tag"] == "topup"], key=lambda l: -l["pnl"])
    return {"eq": np.array(eq), "n_top": n_top, "blocked_days": blocked, "trades": bk.trades, "costs": bk.costs,
            "tops": [{"sym": l["sym"], "day": ymd(int(days[l["day"]])), "usd": round(l["usd"], 2), "pnl": round(l["pnl"], 2)} for l in tops]}


def stats(r: dict, days: np.ndarray, j0: int) -> dict:
    v = r["eq"]
    d = days[j0: j0 + len(v)]
    yrs = np.array([int(ymd(int(t))[:4]) for t in d])
    peak = np.maximum.accumulate(v)
    out = {"final_usd": round(float(v[-1]), 2), "total_pct": round((v[-1] / START_CAP - 1) * 100, 1),
           "max_dd_pct": round(float((v / peak - 1).min()) * 100, 1), "years": {}}
    prev = START_CAP
    for y in sorted(set(yrs)):
        m = np.where(yrs == y)[0]
        out["years"][int(y)] = round((v[m[-1]] / prev - 1) * 100, 1)
        prev = v[m[-1]]
    te = np.where(d > int(dt.datetime.strptime(TRAIN_END, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000))[0]
    base = v[te[0] - 1] if len(te) and te[0] > 0 else START_CAP
    out["train_pct"] = round((base / START_CAP - 1) * 100, 1)
    out["test_pct"] = round((v[-1] / base - 1) * 100, 1)
    vt = v[te] if len(te) else v
    out["test_dd_pct"] = round(float((vt / np.maximum.accumulate(vt) - 1).min()) * 100, 1)
    tops = r["tops"]
    out.update({"n_top": r["n_top"], "trades": r["trades"], "costs_usd": round(r["costs"], 2),
                "top_pnl_usd": round(sum(t["pnl"] for t in tops), 2), "top_win_pct": round(100 * np.mean([t["pnl"] > 0 for t in tops]), 0) if tops else None,
                "best2": tops[:2], "best2_usd": round(sum(t["pnl"] for t in tops[:2]), 2), "worst2": tops[-2:][::-1] if len(tops) > 2 else []})
    return out


VARIANTS = ["A", "B", "C10", "C20", "D10", "D20", "E10", "E20"]
NAMES = {"A": "A · сейчас (ребаланс ядра по понедельникам)", "B": "B · ребаланс ядра каждый день",
         "C": "C · докупка на просадке + перспективы", "D": "D · то же, только BTC/ETH/SOL", "E": "E · докупка без фильтра (контроль)"}


def run(path: str, from15m: bool, name: str) -> dict:
    F = features(load(path, from15m))
    days = F["days"]
    # start: 2023-01-01, or later if the set has no 200-day history for BTC yet (the perps set starts 2022-11)
    j0 = int(np.searchsorted(days, int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)))
    bi = F["syms"].index("BTC")
    while j0 < len(days) and not (np.isfinite(F["sma200"][bi, j0]) and F["first"][bi] + 200 <= j0):
        j0 += 1
    j1 = int(F["last"][bi])
    cache: dict = {}
    res = {}
    for v in VARIANTS:
        r = simulate(F, j0, j1, v, cache)
        res[v] = stats(r, days, j0)
        res[v]["blocked_days"] = r["blocked_days"]
        print(f"[{name}] {v}: total {res[v]['total_pct']}% dd {res[v]['max_dd_pct']}% years {res[v]['years']} top-ups {res[v]['n_top']}", flush=True)
    b = F["Cf"][bi, j0: j1 + 1]
    res["BTC_hold"] = {"total_pct": round((b[-1] / b[0] - 1) * 100, 1), "max_dd_pct": round(float((b / np.maximum.accumulate(b) - 1).min()) * 100, 1)}
    # pick the dip size per family on 2023–2024 only (higher train return; tie → 10 %)
    pick = {}
    for f in "CDE":
        a, c = res[f + "10"], res[f + "20"]
        pick[f] = f + ("20" if c["train_pct"] > a["train_pct"] else "10")
    A = res["A"]
    verdict = {}
    for v in ["B"] + [pick[f] for f in "CDE"]:
        r = res[v]
        ys = [y for y in A["years"] if y in r["years"]]
        better = sum(1 for y in ys if r["years"][y] > A["years"][y])
        extra = r["final_usd"] - A["final_usd"]
        c5 = (extra - r["best2_usd"] > 0) if v != "B" else None
        crit = {"1_total": r["total_pct"] > A["total_pct"], "2_dd": r["max_dd_pct"] >= A["max_dd_pct"] - 2.0,
                "3_years": better >= 3, "years_better": f"{better} из {len(ys)}", "4_test": r["test_pct"] > A["test_pct"],
                "5_not_2_trades": c5, "extra_usd": round(extra, 2)}
        crit["pass"] = all(x for k, x in crit.items() if k[0].isdigit() and x is not None)
        verdict[v] = crit
    days_used = [ymd(int(days[j0])), ymd(int(days[j1]))]
    univ = sorted({F["syms"][i] for j in range(j0, j1 + 1, 7) for i in cache[j]["U"].tolist()})
    return {"name": name, "period": days_used, "coins_in_data": len(F["syms"]), "coins_ever_in_universe": len(univ),
            "bull_share_pct": round(float(F["bull"][j0: j1 + 1].mean()) * 100, 1), "results": res, "picked": pick, "verdict": verdict}


def md(R: dict) -> str:
    res, A = R["results"], R["results"]["A"]
    L = [f"## {R['name']}: {R['period'][0]} → {R['period'][1]}",
         f"Монет в данных {R['coins_in_data']}, побывали в выборке {R['coins_ever_in_universe']}; BTC в «росте» {R['bull_share_pct']}% дней. "
         f"BTC просто держать: {res['BTC_hold']['total_pct']:+.1f}%, худшая просадка {res['BTC_hold']['max_dd_pct']:.1f}%.", "",
         "| Вариант | Итог | Худшая просадка | " + " | ".join(str(y) for y in A["years"]) + " | 2023–24 | 2025–26 | Докупок | Прибыль докупок | 2 лучшие |",
         "|---|---|---|" + "---|" * len(A["years"]) + "---|---|---|---|---|"]
    for v in VARIANTS:
        r = res[v]
        L.append(f"| {v} | {r['total_pct']:+.1f}% | {r['max_dd_pct']:.1f}% | " + " | ".join(f"{r['years'].get(y, 0):+.1f}%" for y in A["years"])
                 + f" | {r['train_pct']:+.1f}% | {r['test_pct']:+.1f}% | {r['n_top']} | ${r['top_pnl_usd']:+,.0f} | ${r['best2_usd']:+,.0f} |")
    L += ["", "Выбор глубины просадки по 2023–2024: " + ", ".join(f"{f} → {v}" for f, v in R["picked"].items()), "",
          "| Вариант | 1 итог выше | 2 просадка не хуже 2 п.п. | 3 лучше в 3 из 4 лет | 4 лучше в 2025–26 | 5 не 1–2 сделки | Лишняя прибыль к A | Вердикт |",
          "|---|---|---|---|---|---|---|---|"]
    yn = {True: "да", False: "нет", None: "—"}
    for v, c in R["verdict"].items():
        L.append(f"| {v} | {yn[c['1_total']]} | {yn[c['2_dd']]} | {yn[c['3_years']]} ({c['years_better']}) | {yn[c['4_test']]} | "
                 f"{yn[c['5_not_2_trades']]} | ${c['extra_usd']:+,.0f} | {'прошёл' if c['pass'] else 'не прошёл'} |")
    L.append("")
    for v in VARIANTS[2:]:
        r = res[v]
        if r["best2"]:
            L.append(f"- {v}: лучшие докупки " + "; ".join(f"{t['sym']} {t['day']} ${t['pnl']:+,.0f}" for t in r["best2"])
                     + (", худшие " + "; ".join(f"{t['sym']} {t['day']} ${t['pnl']:+,.0f}" for t in r["worst2"]) if r["worst2"] else "")
                     + f"; в плюсе {r['top_win_pct']:.0f}% докупок" + (f"; фильтр перспектив не пустил {r['blocked_days']} раз (дней)" if v[0] != "E" else ""))
    return "\n".join(L)


def annotate(path: str) -> None:
    txt = open(path, encoding="utf-8").read()
    chunks, cur = [], ""
    for line in txt.splitlines():
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    for k, c in enumerate(chunks[:10]):
        m = c.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::notice title=spot averaging {k + 1}/{len(chunks)}::{m}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?")
    ap.add_argument("--from15m", action="store_true")
    ap.add_argument("--name", default="spot")
    ap.add_argument("--md")
    ap.add_argument("--json")
    ap.add_argument("--annotate")
    a = ap.parse_args()
    if a.annotate:
        annotate(a.annotate)
        return
    if not a.data:
        sys.exit(__doc__)
    R = run(a.data, a.from15m, a.name)
    text = md(R)
    print(text)
    if a.md:
        with open(a.md, "a", encoding="utf-8") as f:
            f.write(text + "\n\n")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(R, f, ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()

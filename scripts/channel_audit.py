"""An honest check of public Telegram channels the owner reads (18.10.2026: Mr_Trader_Pro, «за полгода ни разу не ошибался»;
29.10.2026: doubletop, trade001k, trade_ultra — «там бывает инфа про ранние проекты, лаунчпад, новости, новые возможности»).

Runs in GitHub Actions only (.github/workflows/research-channel.yml): reads the PUBLIC web preview t.me/s/<channel> back N days
(nothing private, no login), keeps posts only in memory and publishes AGGREGATE numbers — no post texts are stored or committed.
The per-mention rows in the JSON (channel, post number, day, category, ticker, returns) carry no text either.

What it measures, per channel:
  • posts per day, gaps in post numbers (deleted posts or albums — an upper bound), edited share;
  • categories with the SAME classifier the live panel uses (cnq/agents/channel_opps.py): launchpad / IDO / presale, airdrop /
    testnet / points, listing, early project, plain news, ads & referral links (a post can be in several; «primary» = the first);
  • «calls» (coin + long/short word + a price, as on 18.10): with a stop / with targets, and checked on Binance 1h klines;
  • directional forecasts with the live parser (cnq/agents/channel_watch.py): the live rule replayed on history — price at the
    first hour after the post vs 1 day («сегодня / локально») or 3 days later, ±0.5 % = сбылся / не сбылся — and the «always up»
    baseline on the same windows;
  • every coin a post names (first mention of a coin per channel and category in 7 days): close of the post's UTC day → 1 / 7 /
    30 days later, absolute and minus BTC, against a FAIR BASELINE: the 30 Binance coins closest in size (30-day average traded
    volume before that day) on the same days. Also the 7 days BEFORE the post (does the channel write after the move?).
    Coins with no Binance spot pair are measured on OKX daily candles; their baseline is the lower half of Binance by volume;
  • listings and launchpads: the post vs the official announcement (Binance, OKX, Bybit announcement feeds, when reachable from
    the runner): BEFORE or AFTER, and by how many hours; unknown when no announcement of that coin is found within −30…+14 days.
Prices: data.binance.vision (spot1d.npz from scripts/fetch_spot1d.py for daily closes and volume; 1h archives for calls and
forecasts); OKX public candles for OKX-only coins. Stdlib + numpy."""
import argparse
import datetime as dt
import html
import io
import json
import os
import re
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetch_alt100 import http, note  # noqa: E402
from cnq.agents import channel_opps as CO  # noqa: E402
from cnq.agents import channel_watch as CW  # noqa: E402

DAY = 86_400_000
H = 3_600_000
LONG_W = r"(лонг|long|купил|покупк|покупаю|добрал|докупил|набира|вход в лонг|buy)"
SHORT_W = r"(шорт|short|продал в шорт|вход в шорт|sell short)"
RESULT_W = r"(сейчас|уже|тейк|профит|отработал|забрали|\b\d+(?:[.,]\d+)?\s?[xх]\b|\+\d+\s?%)"
LOSS_W = r"(стоп выбил|выбило стоп|по стопу|минус|убыт|закрыл в -|закрыли в -|не отработал|ошибся|loss)"
NOW = dt.datetime.now(dt.timezone.utc)


# ── posts ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_page(b: str) -> list[dict]:
    page = []
    for bl in re.split(r'<div class="tgme_widget_message_wrap', b)[1:]:
        m = re.search(r'data-post="[^"/]+/(\d+)"', bl)
        t = re.search(r'<time datetime="([^"]+)"', bl)
        if not m or not t:
            continue
        tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', bl, re.S)
        raw = tx.group(1) if tx else ""
        links = re.findall(r'href="([^"]+)"', raw)
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", raw))
        text = html.unescape(text)
        when = dt.datetime.fromisoformat(t.group(1).replace("Z", "+00:00"))
        page.append({"id": int(m.group(1)), "t": when, "text": text, "links": [html.unescape(u) for u in links],
                     "edited": "edited" in bl[:4000], "fwd": "tgme_widget_message_forwarded_from" in bl,
                     "media": bool(re.search(r"tgme_widget_message_(photo|video|voice|document)", bl))})
    return page


def fetch_posts(channel: str, days: int) -> tuple[list[dict], str | None]:
    since = NOW - dt.timedelta(days=days)
    posts, before, seen = [], None, set()
    for _ in range(900):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        s, b = http(url, min_interval=1.0)
        if s != 200 or not b:
            if not posts:
                return [], f"HTTP {s} на t.me/s/{channel}"
            break
        page = [p for p in parse_page(b) if p["id"] not in seen]
        if not page:
            if not posts:
                return [], "нет публичного веб-просмотра (t.me/s/ не показывает посты)"
            break
        for p in page:
            seen.add(p["id"])
        posts += page
        oldest = min(page, key=lambda p: p["id"])
        if oldest["t"] < since:
            break
        before = oldest["id"]
    posts = [p for p in posts if p["t"] >= since]
    posts.sort(key=lambda p: p["id"])
    return posts, None


def num(s: str) -> float | None:
    try:
        return float(s.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def classify_call(p: dict, coins: set) -> dict:
    """the 18.10 «call» reading: a coin + a long/short word + a number"""
    tx = p["text"]
    low = tx.lower()
    tags = [c for c in re.findall(r"#([A-Za-z0-9]{2,12})", tx) if c.upper() in coins]
    if not tags:
        tags = [w for w in re.findall(r"\b([A-Z]{2,10})\b", tx) if w in coins and w not in ("USD", "USDT", "CEO", "ETF", "SEC")]
    coin = tags[0].upper() if tags else None
    ml, ms_ = re.search(LONG_W, low), re.search(SHORT_W, low)
    side = ("long" if not ms_ or (ml and ml.start() < ms_.start()) else "short") if (ml or ms_) else None
    nums = [num(x) for x in re.findall(r"(?<![\w#])(\d+(?:[.,]\d+)?)(?![\w%])", tx)]
    nums = [x for x in nums if x and x > 0]
    stop = re.search(r"(стоп|stop|sl)\D{0,12}(\d+(?:[.,]\d+)?)", low)
    tps = re.findall(r"(тейк|цель|цели|tp\d?|target)\D{0,12}(\d+(?:[.,]\d+)?)", low)
    retro = bool(re.search(r"(купил|вход|взяли|добрал|entry)[^\n]{0,40}(сейчас|now|уже)", low))
    kind = ("loss" if re.search(LOSS_W, low) else "retro" if retro else "call" if (coin and side and nums) else
            "result" if (coin and re.search(RESULT_W, low)) else "other")
    entry = None
    if kind == "call":
        m = re.search(LONG_W + r"\D{0,25}(\d+(?:[.,]\d+)?)", low) or re.search(SHORT_W + r"\D{0,25}(\d+(?:[.,]\d+)?)", low)
        entry = num(m.group(2)) if m else (nums[0] if nums else None)
    return {"coin": coin, "side": side, "kind": kind, "entry": entry, "stop": num(stop.group(2)) if stop else None,
            "tps": [num(x[1]) for x in tps if num(x[1])]}


# ── prices ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
class Spot1d:
    """daily closes + quote volume of every Binance spot USDT pair (scripts/fetch_spot1d.py)"""

    def __init__(self, path: str):
        d = np.load(path)
        self.syms = [str(s) for s in d["syms"]]
        self.idx = {s: i for i, s in enumerate(self.syms)}
        for s in list(self.idx):                       # 1000PEPE → PEPE as channels write it
            m = re.match(r"^1000+([A-Z].*)$", s)
            if m and m.group(1) not in self.idx:
                self.idx[m.group(1)] = self.idx[s]
        self.days = d["days"].astype(np.int64)
        self.close = d["close"].astype(np.float64)
        q = d["qvol"].astype(np.float64)
        qz = np.where(np.isfinite(q), q, 0.0)
        cnt = np.isfinite(q).astype(np.float64)
        cs, cc = np.cumsum(qz, 1), np.cumsum(cnt, 1)
        cs = np.concatenate([np.zeros((len(q), 1)), cs], 1)
        cc = np.concatenate([np.zeros((len(q), 1)), cc], 1)
        self.qv30 = np.full(q.shape, np.nan)
        for j in range(30, q.shape[1] + 1):           # mean volume of the 30 days BEFORE day j (no look-ahead)
            if j < q.shape[1]:
                n_ = cc[:, j] - cc[:, j - 30]
                self.qv30[:, j] = np.where(n_ >= 10, (cs[:, j] - cs[:, j - 30]) / np.maximum(n_, 1), np.nan)
        self.btc = self.idx.get("BTC")

    def day(self, t_ms: int) -> int | None:
        j = int((t_ms // DAY * DAY - self.days[0]) // DAY)
        return j if 0 <= j < len(self.days) else None

    def ret(self, i: int, j0: int, j1: int) -> float | None:
        if j1 >= self.close.shape[1] or j0 < 0:
            return None
        a, b = self.close[i, j0], self.close[i, j1]
        return float(b / a - 1) if np.isfinite(a) and np.isfinite(b) and a > 0 else None

    def first_day(self, sym: str) -> int | None:
        i = self.idx.get(sym)
        if i is None:
            return None
        f = np.where(np.isfinite(self.close[i]))[0]
        return int(self.days[f[0]]) if len(f) else None


OKX_D = {}
_okx_lock = threading.Lock()


def okx_daily(sym: str) -> dict:
    """{day_ms: close} from OKX public daily candles (UTC days), the last ~300 days; {} when unreachable"""
    with _okx_lock:
        if sym in OKX_D:
            return OKX_D[sym]
    out, after = {}, None
    for _ in range(4):
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={sym}-USDT&bar=1Dutc&limit=100" + (f"&after={after}" if after else "")
        s, b = http(url, min_interval=0.12)
        try:
            rows = json.loads(b).get("data") or [] if s == 200 else []
        except Exception:  # noqa: BLE001
            rows = []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = float(r[4])
        after = min(int(r[0]) for r in rows)
        if after < (NOW - dt.timedelta(days=300)).timestamp() * 1000:
            break
    with _okx_lock:
        OKX_D[sym] = out
    return out


H1 = {}
_h1_lock = threading.Lock()


def h1(sym: str, start: dt.date) -> np.ndarray | None:
    """1h [open_time_ms, open, high, low, close] of SYMUSDT from `start` to yesterday — Binance spot archive, USDT-M futures as
    fallback; monthly files for whole months, daily files for the current month"""
    with _h1_lock:
        if sym in H1:
            return H1[sym]
    rows = None
    for base in ("https://data.binance.vision/data/spot", "https://data.binance.vision/data/futures/um"):
        got = []
        m = dt.date(start.year, start.month, 1)
        cur = dt.date(NOW.year, NOW.month, 1)
        while m < cur:
            s, b = http(f"{base}/monthly/klines/{sym}USDT/1h/{sym}USDT-1h-{m:%Y-%m}.zip", binary=True)
            if s == 200:
                got += _rows(b)
            m = (m + dt.timedelta(days=32)).replace(day=1)
        d = cur
        while d < NOW.date():
            s, b = http(f"{base}/daily/klines/{sym}USDT/1h/{sym}USDT-1h-{d:%Y-%m-%d}.zip", binary=True)
            if s == 200:
                got += _rows(b)
            d += dt.timedelta(days=1)
        if got:
            a = np.array(sorted(set(got)))
            rows = a
            break
    with _h1_lock:
        H1[sym] = rows
    return rows


def _rows(b: bytes) -> list:
    out = []
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        for line in z.read(z.namelist()[0]).decode().splitlines():
            x = line.split(",")
            if x[0].isdigit():
                t = int(x[0])
                t = t // 1000 if t > 10 ** 14 else t
                out.append((t, float(x[1]), float(x[2]), float(x[3]), float(x[4])))
    return out


def px_at(a: np.ndarray, t_ms: int) -> tuple[int, float] | None:
    """open of the first 1h bar starting at or after t"""
    k = int(np.searchsorted(a[:, 0], t_ms))
    return (k, float(a[k, 1])) if k < len(a) else None


# ── checks ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def check_call(call: dict, p: dict, start: dt.date) -> dict | None:
    a = h1(call["coin"], start)
    if a is None or not call["entry"]:
        return None
    t0 = int(p["t"].timestamp() * 1000)
    k0 = int(np.searchsorted(a[:, 0], t0))
    if k0 >= len(a) - 1:
        return None
    hi, lo, cl = a[k0:, 2], a[k0:, 3], a[k0:, 4]
    px0, e = cl[0], call["entry"]
    if not (0.33 < e / px0 < 3):              # the number is not a price of this coin (a percent, a date, a target...)
        return None
    sgn = 1 if call["side"] == "long" else -1
    filled = bool((lo[:72] <= e).any() and (hi[:72] >= e).any()) or abs(px0 / e - 1) < 0.01

    def ret(h):
        return float(sgn * (cl[h] / e - 1)) if len(cl) > h else None
    w7 = slice(0, min(168, len(cl)))
    mae = float(min(sgn * (lo[w7] / e - 1)) if sgn > 0 else min(sgn * (hi[w7] / e - 1)))
    stop_first = None
    if call["stop"] and call["tps"]:
        st, tp = call["stop"], call["tps"][0]
        hs = np.where((lo <= st) if sgn > 0 else (hi >= st))[0]
        ht = np.where((hi >= tp) if sgn > 0 else (lo <= tp))[0]
        if len(hs) or len(ht):
            stop_first = bool(len(hs) and (not len(ht) or hs[0] < ht[0]))
    return {"filled": filled, "r1d": ret(24), "r7d": ret(168), "r30d": ret(720), "mae7": mae, "stop_first": stop_first}


def replay_forecast(f: dict, t_ms: int, start: dt.date) -> dict | None:
    """the live channel_watch rule on history: open of the first hour after the post → horizon later, ±0.5 %"""
    syms = list(CW.ALTS) if f["coin"] == "ALTS" else [f["coin"]]
    mv = []
    for s in syms:
        a = h1(s, start)
        if a is None:
            continue
        x0 = px_at(a, t_ms)
        x1 = px_at(a, t_ms + f["horizon_h"] * H)
        if not x0 or not x1 or x1[0] == x0[0] or a[x1[0], 0] - a[x0[0], 0] > (f["horizon_h"] + 3) * H:
            continue
        mv.append(x1[1] / x0[1] - 1)
    if len(mv) < (3 if f["coin"] == "ALTS" else 1):
        return None
    m = sum(mv) / len(mv)
    d = m if f["side"] == "up" else -m
    return {"move": m, "result": "hit" if d >= CW.EPS else "miss" if d <= -CW.EPS else "flat"}


def stat(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    v = np.array(xs)
    return {"n": len(v), "median_pct": round(float(np.median(v)) * 100, 1), "mean_pct": round(float(v.mean()) * 100, 1),
            "share_pos_pct": round(float((v > 0).mean()) * 100)}


def measure_mention(S: Spot1d, sym: str, t_ms: int, rng) -> dict | None:
    """close of the post's UTC day → +1 / +7 / +30 days; minus BTC; vs up to 30 similar-size Binance coins on the same days"""
    j0 = S.day(t_ms)
    if j0 is None or S.btc is None:
        return None
    i = S.idx.get(sym)
    out = {"src": "binance"}
    if i is not None and np.isfinite(S.close[i, j0]):
        q = S.qv30[i, j0]
        pool = None
        if np.isfinite(q) and q > 0:                   # the 30 coins closest by 30-day average traded volume that day
            qv = S.qv30[:, j0]
            ok = np.where(np.isfinite(qv) & (qv > 0) & np.isfinite(S.close[:, j0]))[0]
            ok = ok[(ok != i) & (ok != S.btc)]
            pool = ok[np.argsort(np.abs(np.log(qv[ok]) - np.log(q)))[:30]] if len(ok) else None
    else:
        # not on Binance spot that day → OKX daily candles; baseline: the lower half of Binance by volume
        d = okx_daily(sym)
        if not d:
            return None
        day0 = int(S.days[j0])
        if day0 not in d:
            return None
        out["src"] = "okx"
        qv = S.qv30[:, j0]
        ok = np.where(np.isfinite(qv))[0]
        pool = ok[qv[ok] <= np.nanmedian(qv[ok])] if len(ok) else None
        pool = pool[pool != S.btc] if pool is not None else None
    if pool is not None and len(pool) > 30:            # OKX-only coins: 30 of the lower half, fixed seed per post
        pool = rng.choice(pool, 30, replace=False)
    for h in (1, 7, 30):
        j1 = j0 + h
        rb = S.ret(S.btc, j0, j1)
        if out["src"] == "binance":
            r = S.ret(i, j0, j1)
        else:
            d1 = int(S.days[j0]) + h * DAY
            r = (d[d1] / d[int(S.days[j0])] - 1) if d1 in d else None
        if r is None or rb is None:
            continue
        out[f"r{h}"] = r
        out[f"b{h}"] = rb
        if pool is not None and len(pool):
            br = [S.ret(int(k), j0, j1) for k in pool]
            br = [x - rb for x in br if x is not None]
            if len(br) >= 5:
                out[f"base{h}"] = float(np.mean(br))               # the baseline coin's move minus BTC, averaged
                out[f"base_beat{h}"] = float(np.mean([x > 0 for x in br]))
    if out["src"] == "binance":
        pre = S.ret(i, j0 - 7, j0)
        preb = S.ret(S.btc, j0 - 7, j0)
        if pre is not None and preb is not None:
            out["pre7"] = pre - preb
    return out if "r1" in out else None


def mention_summary(rows: list[dict]) -> dict:
    res = {"n": len(rows)}
    for h in (1, 7, 30):
        v = [r for r in rows if f"r{h}" in r]
        if not v:
            continue
        rel = np.array([r[f"r{h}"] - r[f"b{h}"] for r in v])
        vb = [r for r in v if f"base{h}" in r]
        d = np.array([r[f"r{h}"] - r[f"b{h}"] - r[f"base{h}"] for r in vb])
        base = np.array([r[f"base{h}"] for r in vb])
        res[f"d{h}"] = {
            "n": len(v), "median_abs_pct": round(float(np.median([r[f"r{h}"] for r in v])) * 100, 1),
            "median_vs_btc_pct": round(float(np.median(rel)) * 100, 1), "mean_vs_btc_pct": round(float(rel.mean()) * 100, 1),
            "beat_btc_pct": round(float((rel > 0).mean()) * 100),
            "baseline_n": len(vb),
            "baseline_median_vs_btc_pct": round(float(np.median(base)) * 100, 1) if len(vb) else None,
            "baseline_beat_btc_pct": round(float(np.mean([r[f"base_beat{h}"] for r in vb])) * 100) if len(vb) else None,
            "edge_vs_baseline_mean_pct": round(float(d.mean()) * 100, 1) if len(d) else None,
            "edge_vs_baseline_median_pct": round(float(np.median(d)) * 100, 1) if len(d) else None,
            "edge_2se_pct": round(float(2 * d.std(ddof=1) / np.sqrt(len(d))) * 100, 1) if len(d) > 2 else None,
        }
    pre = [r["pre7"] for r in rows if "pre7" in r]
    if pre:
        res["pre7_median_vs_btc_pct"] = round(float(np.median(pre)) * 100, 1)
        res["pre7_up_pct"] = round(float(np.mean([x > 0 for x in pre])) * 100)
    return res


# ── official announcements (best effort: what the runner can reach) ─────────────────────────────────────────────────────
TITLE_TICK = re.compile(r"\b([A-Z][A-Z0-9]{1,11})\b")
TITLE_STOP = set("USDT USDC FDUSD BNB USD OKX BINANCE BYBIT NEW API VIP HODLER ALPHA TGE IDO TO ON AND FOR THE WILL OF IN AT "
                 "SPOT USDⓈ USDS UTC PERP MARGIN".split())


def announcements(since_ms: int) -> tuple[list[dict], dict]:
    """[{venue, t, kind, ticks}] from the exchanges' public announcement feeds; {venue: "ok N" | "HTTP …"}"""
    out, probe = [], {}

    def add(venue, t, title, kind):
        ticks = {x for x in TITLE_TICK.findall(title or "") if x not in TITLE_STOP}
        ticks |= set(re.findall(r"\(([A-Z0-9]{2,12})\)", title or ""))
        if ticks:
            out.append({"venue": venue, "t": int(t), "kind": kind, "ticks": sorted(ticks)})

    # Binance: «New Cryptocurrency Listing» (48) — listings, Launchpool, HODLer Airdrops, Megadrop
    n = 0
    for base in ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                 "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query"):
        st = None
        for page in range(1, 40):
            s, b = http(f"{base}?type=1&pageNo={page}&pageSize=20&catalogId=48", min_interval=0.5)
            st = s
            try:
                arts = ((json.loads(b).get("data") or {}).get("catalogs") or [{}])[0].get("articles") or [] if s == 200 else []
            except Exception:  # noqa: BLE001
                arts = []
            if not arts:
                break
            for a in arts:
                t = a.get("releaseDate") or 0
                title = a.get("title") or ""
                kind = "launchpad" if re.search(r"launchpool|launchpad|megadrop|hodler|airdrop", title, re.I) else "listing"
                add("binance", t, title, kind)
                n += 1
            if min(a.get("releaseDate") or 0 for a in arts) < since_ms:
                break
        if n:
            break
    probe["binance"] = f"ok {n}" if n else f"HTTP {st}"
    # OKX: new listings + Jumpstart
    for ann, kind in (("announcements-new-listings", "listing"), ("announcements-jumpstart", "launchpad")):
        n, st = 0, None
        for page in range(1, 30):
            s, b = http(f"https://www.okx.com/api/v5/support/announcements?annType={ann}&page={page}", min_interval=0.3)
            st = s
            try:
                det = (json.loads(b).get("data") or [{}])[0].get("details") or [] if s == 200 else []
            except Exception:  # noqa: BLE001
                det = []
            if not det:
                break
            for a in det:
                add("okx", int(a.get("pTime") or 0), a.get("title") or "", kind)
                n += 1
            if min(int(a.get("pTime") or 0) for a in det) < since_ms:
                break
        probe[f"okx {kind}"] = f"ok {n}" if n else f"HTTP {st}"
    # Bybit: new crypto
    n, st = 0, None
    for page in range(1, 30):
        s, b = http(f"https://api.bybit.com/v5/announcements/index?locale=en-US&type=new_crypto&page={page}&limit=50", min_interval=0.3)
        st = s
        try:
            lst = (json.loads(b).get("result") or {}).get("list") or [] if s == 200 else []
        except Exception:  # noqa: BLE001
            lst = []
        if not lst:
            break
        for a in lst:
            t = a.get("publishTime") or a.get("dateTimestamp") or 0
            add("bybit", t, a.get("title") or "", "launchpad" if re.search(r"launchpool|launchpad|token splash", a.get("title") or "", re.I) else "listing")
            n += 1
        if min(int(a.get("publishTime") or a.get("dateTimestamp") or 0) for a in lst) < since_ms:
            break
    probe["bybit"] = f"ok {n}" if n else f"HTTP {st}"
    return out, probe


def vs_announcement(tick: str, venue: str | None, t_ms: int, anns: list[dict]) -> dict | None:
    """the earliest official announcement of this coin within −30…+14 days of the post (the named venue first)"""
    c = [a for a in anns if tick in a["ticks"] and t_ms - 30 * DAY <= a["t"] <= t_ms + 14 * DAY]
    if venue:
        cv = [a for a in c if a["venue"] == venue]
        c = cv or c
    if not c:
        return None
    a = min(c, key=lambda x: x["t"])
    return {"venue": a["venue"], "lead_h": round((a["t"] - t_ms) / H, 1)}       # > 0: the post came BEFORE the announcement


# ── the audit ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def audit(channel: str, days: int, S: Spot1d | None, okx_set: set, anns: list[dict], coins_cg: set) -> dict:
    posts, err = fetch_posts(channel, days)
    if not posts:
        return {"channel": channel, "error": err or "нет постов"}
    start = (posts[0]["t"] - dt.timedelta(days=2)).date()
    ids = [p["id"] for p in posts]
    span = max(1.0, (posts[-1]["t"] - posts[0]["t"]).total_seconds() / 86400)
    known = set(coins_cg) | set(okx_set) | (set(S.idx) if S else set())
    out = {"channel": channel, "days": days, "posts": len(posts), "first": posts[0]["t"].strftime("%Y-%m-%d"),
           "last": posts[-1]["t"].strftime("%Y-%m-%d"), "per_day": round(len(posts) / span, 1), "id_range": [ids[0], ids[-1]],
           "id_gaps_max_deleted": (ids[-1] - ids[0] + 1) - len(ids),
           "edited_pct": round(sum(p["edited"] for p in posts) / len(posts) * 100),
           "forwarded_pct": round(sum(p["fwd"] for p in posts) / len(posts) * 100),
           "media_pct": round(sum(p["media"] for p in posts) / len(posts) * 100),
           "empty_text_pct": round(sum(1 for p in posts if not p["text"].strip()) / len(posts) * 100)}
    # categories (the live classifier)
    ex = [(p, CO.extract(p["text"], p["links"], known, okx_set)) for p in posts]
    multi, prim = {}, {}
    for _, e in ex:
        for c in e["cats"]:
            multi[c] = multi.get(c, 0) + 1
        k = e["cat"] or ("ad" if e["promo"] else "other")
        prim[k] = prim.get(k, 0) + 1
    out["categories_any"] = multi
    out["categories_primary"] = prim
    out["ref_link_posts"] = sum(1 for _, e in ex if e["ref_link"])
    out["promo_pct"] = round(sum(1 for _, e in ex if e["promo"]) / len(posts) * 100)
    venues = {}
    for _, e in ex:
        if e["cat"] in ("listing", "launchpad") and e["venue"]:
            venues[e["venue"]] = venues.get(e["venue"], 0) + 1
    out["listing_launchpad_venues"] = dict(sorted(venues.items(), key=lambda x: -x[1])[:8])
    # calls (18.10 reading) checked on 1h
    cls = [(p, classify_call(p, known)) for p in posts]
    calls = [(p, c) for p, c in cls if c["kind"] == "call"]
    with ThreadPoolExecutor(8) as tp:
        res = list(tp.map(lambda pc: check_call(pc[1], pc[0], start), calls))
    checked = [r for r in res if r]
    filled = [r for r in checked if r["filled"]]
    out["calls"] = {"n": len(calls), "with_stop_pct": round(sum(1 for _, c in calls if c["stop"]) / max(1, len(calls)) * 100),
                    "with_targets_pct": round(sum(1 for _, c in calls if c["tps"]) / max(1, len(calls)) * 100),
                    "checked": len(checked), "entry_reached": len(filled),
                    "long": sum(1 for _, c in calls if c["side"] == "long"), "short": sum(1 for _, c in calls if c["side"] == "short"),
                    "in_direction_1d": stat([r["r1d"] for r in filled]), "in_direction_7d": stat([r["r7d"] for r in filled]),
                    "in_direction_30d": stat([r["r30d"] for r in filled]), "worst_against_7d": stat([r["mae7"] for r in filled]),
                    "stop_before_target": sum(1 for r in filled if r["stop_first"] is True),
                    "target_before_stop": sum(1 for r in filled if r["stop_first"] is False),
                    "retro_posts": sum(1 for _, c in cls if c["kind"] == "retro"), "loss_posts": sum(1 for _, c in cls if c["kind"] == "loss"),
                    "result_posts": sum(1 for _, c in cls if c["kind"] == "result")}
    # directional forecasts — the live parser and rule replayed
    coins_live = set(coins_cg) | {"BTC", "ETH"}
    fcs = []
    for p in posts:
        for f in CW.forecasts(p["text"], coins_live):
            fcs.append((p, f))
    with ThreadPoolExecutor(8) as tp:
        rr = list(tp.map(lambda pf: replay_forecast(pf[1], int(pf[0]["t"].timestamp() * 1000), start), fcs))
    done = [(pf, r) for pf, r in zip(fcs, rr) if r]
    n = len(done)
    hits = sum(1 for _, r in done if r["result"] == "hit")
    miss = sum(1 for _, r in done if r["result"] == "miss")
    up = sum(1 for _, r in done if r["move"] >= CW.EPS)
    dn = sum(1 for _, r in done if r["move"] <= -CW.EPS)
    out["forecasts"] = {"found": len(fcs), "checked": n, "hits": hits, "misses": miss, "flat": n - hits - miss,
                        "hit_rate_pct": round(hits / n * 100) if n else None, "always_up_pct": round(up / n * 100) if n else None,
                        "always_down_pct": round(dn / n * 100) if n else None,
                        "up_share_pct": round(sum(1 for (_, f), _ in done if f["side"] == "up") / n * 100) if n else None,
                        "avg_move_in_direction_pct": round(float(np.mean([r["move"] if f["side"] == "up" else -r["move"] for (_, f), r in done])) * 100, 2) if n else None,
                        "coins": dict(sorted({c: sum(1 for (_, f), _ in done if f["coin"] == c) for c in {f["coin"] for (_, f), _ in done}}.items(), key=lambda x: -x[1])[:8])}
    # mentions of coins: first mention per (coin, category) in 7 days
    rows, last_seen = [], {}
    if S:
        cand = []
        for p, e in ex:
            t_ms = int(p["t"].timestamp() * 1000)
            cat = e["cat"] or ("call" if CW.forecasts(p["text"], coins_live) else "other")
            for tk in e["tickers"][:3]:
                if t_ms - last_seen.get((tk, cat), -10 ** 15) < 7 * DAY:      # the same coin again within 7 days: counted once
                    continue
                last_seen[(tk, cat)] = t_ms
                cand.append((p, e, tk, t_ms, cat))
        with ThreadPoolExecutor(8) as tp:
            ms = list(tp.map(lambda c: measure_mention(S, c[2], c[3], np.random.default_rng(c[3] % 2 ** 32)), cand))
        for (p, e, tk, t_ms, cat), m in zip(cand, ms):
            if not m:
                continue
            row = {"post": p["id"], "day": p["t"].strftime("%Y-%m-%d"), "ticker": tk, "cat": cat, "promo": e["promo"],
                   "okx": tk in okx_set, **{k: (round(v, 5) if isinstance(v, float) else v) for k, v in m.items()}}
            if cat in ("listing", "launchpad"):
                va = vs_announcement(tk, e["venue"], t_ms, anns)
                if va:
                    row["ann_venue"], row["ann_lead_h"] = va["venue"], va["lead_h"]
                fd = S.first_day(tk)
                if fd and t_ms - 30 * DAY <= fd <= t_ms + 30 * DAY:
                    row["binance_spot_first_day"] = dt.datetime.fromtimestamp(fd / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
                    row["after_binance_spot_start"] = t_ms >= fd + DAY        # posted after the first full trading day began
            rows.append(row)
        out["mentions_unique_7d"] = len(cand)
        out["mentions_measured"] = len(rows)
        out["mentions_okx_only"] = sum(1 for r in rows if r["src"] == "okx")
        out["mentions_all"] = mention_summary(rows)
        by = {}
        for r in rows:
            by.setdefault(r["cat"], []).append(r)
        out["mentions_by_cat"] = {k: mention_summary(v) for k, v in sorted(by.items(), key=lambda x: -len(x[1]))}
        out["mentions_promo"] = mention_summary([r for r in rows if r["promo"]])
        ll = [r for r in rows if r["cat"] in ("listing", "launchpad")]
        known_ann = [r for r in ll if "ann_lead_h" in r]
        out["listing_launchpad_vs_announcement"] = {
            "posts_with_coin": len(ll), "announcement_found": len(known_ann),
            "before": sum(1 for r in known_ann if r["ann_lead_h"] > 0), "after": sum(1 for r in known_ann if r["ann_lead_h"] <= 0),
            "before_by_more_than_24h": sum(1 for r in known_ann if r["ann_lead_h"] > 24),
            "median_lead_h": round(float(np.median([r["ann_lead_h"] for r in known_ann])), 1) if known_ann else None,
            "after_binance_spot_start": sum(1 for r in ll if r.get("after_binance_spot_start") is True),
            "before_binance_spot_start": sum(1 for r in ll if r.get("after_binance_spot_start") is False)}
        out["coins_top"] = dict(sorted({r["ticker"]: sum(1 for x in rows if x["ticker"] == r["ticker"]) for r in rows}.items(), key=lambda x: -x[1])[:15])
    out["rows"] = rows
    return out


def short(o: dict) -> str:
    """one line per channel for the annotation (the full JSON goes to the release asset)"""
    if o.get("error"):
        return f"{o['channel']}: {o['error']}"
    m = o.get("mentions_all") or {}
    d7 = m.get("d7") or {}
    f = o.get("forecasts") or {}
    return (f"{o['posts']} постов {o['first']}→{o['last']} ({o['per_day']}/день), реклама {o['promo_pct']}%, категории {o['categories_primary']}; "
            f"звонков {o['calls']['n']} (со стопом {o['calls']['with_stop_pct']}%); прогнозов проверено {f.get('checked')}, сбылось {f.get('hit_rate_pct')}% "
            f"vs «всегда вверх» {f.get('always_up_pct')}%; монет {o.get('mentions_measured')}: 7 дн к BTC медиана {d7.get('median_vs_btc_pct')}% "
            f"(база {d7.get('baseline_median_vs_btc_pct')}%), перевес к базе {d7.get('edge_vs_baseline_mean_pct')}±{d7.get('edge_2se_pct')}%; "
            f"листинги/лаунчпады vs анонс {o.get('listing_launchpad_vs_announcement')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="Mr_Trader_Pro", help="one or several, comma separated")
    ap.add_argument("--days", type=int, default=200)
    ap.add_argument("--spot1d", default="spot1d.npz")
    ap.add_argument("--out", default="channel_audit.json")
    a = ap.parse_args()
    s, b = http("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1")
    coins_cg = {c["symbol"].upper() for c in json.loads(b)} if s == 200 else set()
    coins_cg |= {"BTC", "ETH", "SOL", "HYPE", "ARB", "SUI", "AAVE", "UNI", "BNB", "XRP", "DOGE", "LINK", "ONDO", "TON", "NEAR", "ENA", "PEPE"}
    s, b = http("https://www.okx.com/api/v5/public/instruments?instType=SPOT")
    try:
        okx_set = {x["baseCcy"] for x in json.loads(b).get("data") or [] if x.get("quoteCcy") in ("USDT", "USDC")} if s == 200 else set()
    except Exception:  # noqa: BLE001
        okx_set = set()
    S = Spot1d(a.spot1d) if os.path.exists(a.spot1d) else None
    since = int((NOW - dt.timedelta(days=a.days + 30)).timestamp() * 1000)
    anns, probe = announcements(since)
    note("sources", json.dumps({"coingecko_top": len(coins_cg), "okx_spot": len(okx_set), "spot1d": len(S.syms) if S else None,
                                "spot1d_last": dt.datetime.fromtimestamp(int(S.days[-1]) / 1000, dt.timezone.utc).strftime("%Y-%m-%d") if S else None,
                                "announcements": probe}, ensure_ascii=False))
    res = {"asof": NOW.strftime("%Y-%m-%d %H:%M UTC"), "days": a.days, "sources": probe, "channels": {}}
    for ch in [c.strip() for c in a.channel.split(",") if c.strip()]:
        try:
            o = audit(ch, a.days, S, okx_set, anns, coins_cg)
        except Exception as e:  # noqa: BLE001
            o = {"channel": ch, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        res["channels"][ch] = o
        note(f"channel {ch}", short(o), "warning" if o.get("error") else "notice")
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("## Проверка каналов\n```\n" + json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in res["channels"].items()},
                                                             ensure_ascii=False, indent=1) + "\n```\n")


if __name__ == "__main__":
    main()

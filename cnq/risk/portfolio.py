"""Портфельный риск-слой демо-счетов — шаг движка `risk`, каждую минуту перед столами (20.10.2026).

Читает:  web/paper.json — все демо-счета движка (cons, bal, aggr, lab, brk, imp, imps, exp — paper_desk; own, lst, lsn, lstx, btcx);
         data/cnq.sqlite — свежесть свечей (по ним считают столы 15m) и дневные закрытия для корреляции;
         data/agents.sqlite (только чтение) — own_closes (дневные закрытия ~130 монет) и risk_skips (пропущенные сигналы);
         data/kill_switch.json, data/conveyor_state.json, data/risk_reset.json.
Считает: брутто / нетто по счёту и всего, плечо счёта (брутто / капитал), долю монеты, кластеры похожих монет (корреляция дневных
         доходностей за 30 дней > 0.8 у каждой пары), результат за день UTC и просадку от пика у всех счетов вместе, свежесть данных.
Пишет:   data/risk_state.json — полное состояние (его читает шлюз gate.allow_entry), web/risk.json — публичная сводка (ru/en).

Результат всех счетов вместе — индекс с цепным связыванием: за каждый шаг складываются изменения капитала (с учётом открытых
позиций) только тех счетов, что были и в прошлом шаге с тем же началом (since). Новый стол, обнулённый стол или скачок > 50 % за
минуту (сбой данных) не дают ложной прибыли или просадки. Шаг никогда не падает: ошибка → прошлое состояние + текст ошибки,
и через 10 минут без обновления шлюз сам закрывает входы. Только stdlib."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time

from . import gate as G
from . import limits as L

DAY = 86_400_000
BTC_LEV = 2.0          # = cnq/agents/turbo_desk.BTC_LEV: счёт btcx «в рынке» держит BTC на 2 капитала (строк open у него нет)
JUMP_MAX = 0.5         # изменение капитала счёта за один шаг больше 50 % — не торговля, а сбой/обнуление: в индекс не идёт

NAMES_EN = {"daily_loss": "Daily result, all demo accounts (UTC day)", "drawdown": "Drawdown from peak, all demo accounts",
            "leverage": "Account leverage (gross / equity), worst account", "coin": "One coin, share of gross",
            "cluster": "Cluster of similar coins (ρ > 0.8), net share of gross", "data_age": "Market data age"}
NAMES_RU = {"daily_loss": "Результат всех демо-счетов за день (UTC)", "drawdown": "Просадка всех демо-счетов от пика",
            "leverage": "Плечо счёта (брутто / капитал), худший счёт", "coin": "Одна монета, доля брутто",
            "cluster": "Кластер похожих монет (ρ > 0.8), чистая доля брутто", "data_age": "Возраст рыночных данных"}


def norm(sym) -> str:
    s = str(sym or "").upper()
    for suf in ("-USDT", "USDT", "-PERP"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    return s


# ───────────────────────────────────────────────────────────────────────────────────────────── exposure
def accounts_from_paper(paper: dict) -> dict:
    """{key: {name, equity (mtm), gross, net, leverage, positions [(sym, signed $)], since, asof, n_open}} from web/paper.json"""
    out = {}
    for key, p in ((paper or {}).get("profiles") or {}).items():
        if not isinstance(p, dict) or p.get("equity") is None:
            continue
        try:
            if p.get("equity_mtm") is not None:
                mtm = float(p["equity_mtm"])
            else:
                mtm = float(p["equity"]) + float(p.get("open_pnl_usd") or 0)
            pos = []
            for o in p.get("open") or []:
                q = o.get("qty_left") if o.get("qty_left") is not None else o.get("qty")
                px = o.get("price") or o.get("entry")
                if not q or not px:
                    continue
                n = abs(float(q) * float(px))
                pos.append((norm(o.get("symbol")), n if str(o.get("side", "LONG")).upper() != "SHORT" else -n))
            if key == "btcx" and p.get("in_market"):
                pos.append(("BTC", BTC_LEV * float(p["equity"])))
            gross = sum(abs(n) for _, n in pos)
            out[key] = {"name": p.get("name") or key, "equity": round(mtm, 2), "gross": round(gross, 2),
                        "net": round(sum(n for _, n in pos), 2), "leverage": round(gross / mtm, 4) if mtm > 0 else None,
                        "positions": [(s, round(n, 2)) for s, n in pos], "n_open": len(pos),
                        "since": (p.get("reset") or {}).get("time") or p.get("since"), "asof": p.get("asof"),
                        "mirror_of": next((b for b, ms in L.MIRRORS.items() if key in [m for m, _ in ms]), None)}
        except (TypeError, ValueError):
            continue
    return out


def coins_of(accounts: dict) -> dict:
    out: dict = {}
    for k, a in accounts.items():
        for s, n in a["positions"]:
            c = out.setdefault(s, {"gross": 0.0, "net": 0.0, "accounts": []})
            c["gross"] += abs(n)
            c["net"] += n
            if k not in c["accounts"]:
                c["accounts"].append(k)
    for c in out.values():
        c["gross"], c["net"] = round(c["gross"], 2), round(c["net"], 2)
    return out


# ───────────────────────────────────────────────────────────────────────────────────────────── correlation clusters
def daily_closes(agents_db: str | None, candles_db: str | None, need: set, now: int, days: int = L.CORR_DAYS) -> dict:
    """{SYM: {day_ms: close}} for the last days+3 complete UTC days: own_closes (agents.sqlite) first, then the 15m store for the
    coins it lacks (the last 15m close of each day)."""
    today = now // DAY * DAY
    start = today - (days + 3) * DAY
    out: dict = {}
    if agents_db and os.path.exists(agents_db):
        try:
            c = sqlite3.connect(f"file:{agents_db}?mode=ro", uri=True, timeout=5)
            try:
                for s, d, cl in c.execute("SELECT symbol, day, close FROM own_closes WHERE day >= ? AND day < ?", (start, today)):
                    if cl:
                        out.setdefault(norm(s), {})[int(d)] = float(cl)
            finally:
                c.close()
        except sqlite3.Error:
            pass
    lack = [s for s in need if len(out.get(s) or {}) < days]
    if lack and candles_db and os.path.exists(candles_db):
        try:
            c = sqlite3.connect(f"file:{candles_db}?mode=ro", uri=True, timeout=5)
            try:
                for s in lack:
                    rows = c.execute("SELECT open_time, close FROM candles WHERE symbol=? AND interval='15m' AND open_time>=? AND open_time<? "
                                     "ORDER BY open_time", (f"{s}-USDT", start, today)).fetchall()
                    dd: dict = {}
                    for t, cl in rows:
                        if cl:
                            dd[int(t) // DAY * DAY] = float(cl)      # the last 15m close of the day wins
                    if len(dd) > len(out.get(s) or {}):
                        out[s] = dd
            finally:
                c.close()
        except sqlite3.Error:
            pass
    return out


def returns(closes: dict, now: int, days: int = L.CORR_DAYS) -> dict:
    """{day: return} over the last `days` complete days (a return needs both days present)"""
    today = now // DAY * DAY
    grid = [today - (days + 1 - i) * DAY for i in range(days + 1)]
    r = {}
    for a, b in zip(grid, grid[1:]):
        if closes.get(a) and closes.get(b):
            r[b] = closes[b] / closes[a] - 1
    return r


def corr(ra: dict, rb: dict, min_n: int = 20) -> float | None:
    ks = [k for k in ra if k in rb]
    if len(ks) < min_n:
        return None
    xa = [ra[k] for k in ks]
    xb = [rb[k] for k in ks]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    va = sum((x - ma) ** 2 for x in xa)
    vb = sum((x - mb) ** 2 for x in xb)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / math.sqrt(va * vb)


def clusters(rets: dict, weight: dict, rho: float = L.CLUSTER_RHO) -> list[list[str]]:
    """Greedy complete linkage: the coin with the largest exposure starts a cluster, a coin joins only if its correlation with EVERY
    member is above rho. Deterministic (exposure, then name). Coins without enough history stay alone."""
    order = sorted(rets, key=lambda s: (-weight.get(s, 0.0), s))
    cache: dict = {}

    def rr(a, b):
        k = (a, b) if a < b else (b, a)
        if k not in cache:
            cache[k] = corr(rets[a], rets[b])
        return cache[k]
    done: set = set()
    out = []
    for s in order:
        if s in done:
            continue
        mem = [s]
        for t in order:
            if t in done or t in mem:
                continue
            if all((rr(t, m) or -1) > rho for m in mem):
                mem.append(t)
        done.update(mem)
        out.append(mem)
    return out


# ───────────────────────────────────────────────────────────────────────────────────────────── freshness
def data_freshness(candles_db: str | None, now: int) -> dict:
    """The age of the market data the 15m desks act on. last_ts = min(last write of the candle store, the end of the newest stored
    BTC 15m bar): a store that stopped being written ages from its last write; a store written with no new bars ages once the
    newest bar should have been replaced. None when neither is known."""
    out = {"last_ts": None, "age_min": None, "store_write": None, "bar_open": None, "source": "data/cnq.sqlite · BTC-USDT 15m"}
    if not candles_db:
        return out
    mt = [os.path.getmtime(p) for p in (candles_db, candles_db + "-wal") if os.path.exists(p)]
    if mt:
        out["store_write"] = int(max(mt) * 1000)
    if os.path.exists(candles_db):
        try:
            c = sqlite3.connect(f"file:{candles_db}?mode=ro", uri=True, timeout=5)
            try:
                r = c.execute("SELECT MAX(open_time) FROM candles WHERE symbol='BTC-USDT' AND interval='15m'").fetchone()
            finally:
                c.close()
            if r and r[0]:
                out["bar_open"] = int(r[0])
        except sqlite3.Error:
            pass
    cand = [x for x in (out["store_write"], out["bar_open"] + 900_000 if out["bar_open"] else None) if x]
    if cand:
        out["last_ts"] = min(cand)
        out["age_min"] = round(max(0.0, (now - out["last_ts"]) / 60_000), 2)
    return out


# ───────────────────────────────────────────────────────────────────────────────────────────── P&L index
def update_index(prev: dict, accounts: dict, now: int) -> tuple[dict, list]:
    """chain-linked result of all accounts together → (index, notes)"""
    ix = dict(prev.get("index") or {})
    value = float(ix.get("value") or 1.0)
    peak = float(ix.get("peak") or value)
    day = ix.get("day")
    day_open = float(ix.get("day_open") or value)
    day_pnl = float(ix.get("day_pnl_usd") or 0.0)
    prev_acc = prev.get("prev_accounts") or {}
    pnl = cap = 0.0
    notes = []
    for k, a in accounts.items():
        p = prev_acc.get(k)
        if not p or p.get("since") != a.get("since") or not p.get("mtm"):
            continue
        d = a["equity"] - float(p["mtm"])
        if abs(d) > JUMP_MAX * abs(float(p["mtm"])):
            notes.append(f"{k}: скачок капитала {d:+.0f}$ за шаг — не в индексе")
            continue
        pnl += d
        cap += float(p["mtm"])
    today = G.utc_day(now)
    if day != today:
        day, day_open, day_pnl = today, value, 0.0
    if cap > 0:
        value *= 1 + pnl / cap
    peak = max(peak, value)
    day_pnl += pnl
    return {"value": value, "peak": peak, "day": day, "day_open": day_open, "day_pnl_usd": round(day_pnl, 2),
            "daily": value / day_open - 1 if day_open else 0.0, "drawdown": value / peak - 1 if peak else 0.0,
            "started": ix.get("started") or now, "last_step_pnl_usd": round(pnl, 2)}, notes


# ───────────────────────────────────────────────────────────────────────────────────────────── limits
def _status(pct: float | None, inclusive: bool = False) -> str:
    if pct is None:
        return "unknown"
    if pct > 1 or (inclusive and pct >= 1):
        return "breach"
    return "warn" if pct >= L.WARN_AT else "ok"


def limit_rows(ix: dict, accounts: dict, coins: dict, cls: list, data: dict, comb: dict) -> list[dict]:
    denom = max(comb["gross"], comb["equity"]) or 0.0
    lev = max(((a["leverage"] or 0.0, k) for k, a in accounts.items() if a.get("leverage") is not None), default=(0.0, None))
    coin = max(((c["gross"] / denom if denom else 0.0, s) for s, c in coins.items()), default=(0.0, None))
    cl = max(((abs(c["net"]) / denom if denom else 0.0, c) for c in cls if len(c["members"]) > 1), default=(0.0, None), key=lambda x: x[0])
    rows = [
        {"key": "daily_loss", "value": round(ix["daily"], 5), "limit": L.DAILY_LOSS_HALT, "unit": "share", "action": "halt",
         "pct": round(max(0.0, ix["daily"] / L.DAILY_LOSS_HALT), 3), "inclusive": True},
        {"key": "drawdown", "value": round(ix["drawdown"], 5), "limit": L.DRAWDOWN_HALT, "unit": "share", "action": "halt",
         "pct": round(max(0.0, ix["drawdown"] / L.DRAWDOWN_HALT), 3), "inclusive": True},
        {"key": "leverage", "value": round(lev[0], 3), "limit": L.LEVERAGE_MAX, "unit": "x", "action": "block",
         "pct": round(lev[0] / L.LEVERAGE_MAX, 3), "account": lev[1]},
        {"key": "coin", "value": round(coin[0], 4), "limit": L.COIN_MAX, "unit": "share", "action": "block",
         "pct": round(coin[0] / L.COIN_MAX, 3), "coin": coin[1]},
        {"key": "cluster", "value": round(cl[0], 4), "limit": L.CLUSTER_MAX, "unit": "share", "action": "block",
         "pct": round(cl[0] / L.CLUSTER_MAX, 3), "members": (cl[1] or {}).get("members")},
        {"key": "data_age", "value": data.get("age_min"), "limit": L.DATA_MAX_AGE_MIN, "unit": "min", "action": "halt",
         "pct": None if data.get("age_min") is None else round(data["age_min"] / L.DATA_MAX_AGE_MIN, 3)},
    ]
    for r in rows:
        r["status"] = "breach" if r["key"] == "data_age" and r["value"] is None else _status(r["pct"], r.pop("inclusive", False))
        r["name"] = {"ru": NAMES_RU[r["key"]], "en": NAMES_EN[r["key"]]}
    return rows


def _fmt(r: dict) -> str:
    v = r["value"]
    if v is None:
        return "—"
    if r["unit"] == "share":
        return f"{v * 100:+.2f}%" if r["key"] in ("daily_loss", "drawdown") else f"{v * 100:.1f}%"
    return f"{v:.2f}×" if r["unit"] == "x" else f"{v:.1f} min"


def blocks_of(rows: dict, accounts: dict, coins: dict, cls: list, comb: dict, latch: dict, kill: dict, conveyor: dict,
              data: dict) -> list[dict]:
    out = []
    if kill.get("on"):
        out.append({"kind": "kill", "ru": "Аварийный выключатель включён — новые входы запрещены на всех демо-счетах",
                    "en": "Kill switch is ON — no new entries on any demo account"})
    if latch.get("daily"):
        v = latch["daily"].get("value") or 0
        out.append({"kind": "daily_halt", "ru": f"Дневной стоп: все демо-счета за день {v * 100:+.2f}% (лимит −3%) — новые входы с 00:00 UTC",
                    "en": f"Daily stop: all demo accounts {v * 100:+.2f}% today (limit −3%) — entries resume at 00:00 UTC"})
    if latch.get("drawdown"):
        v = latch["drawdown"].get("value") or 0
        out.append({"kind": "drawdown_halt", "ru": f"Стоп по просадке: {v * 100:+.2f}% от пика (лимит −15%) — до сброса владельцем",
                    "en": f"Drawdown stop: {v * 100:+.2f}% from peak (limit −15%) — until the owner resets it"})
    if data.get("age_min") is None or data["age_min"] > L.DATA_MAX_AGE_MIN:
        a = data.get("age_min")
        out.append({"kind": "stale_data", "ru": f"Рыночные данные устарели ({'нет данных' if a is None else f'{a:.0f} мин'}, лимит 10 мин) — входы закрыты",
                    "en": f"Market data is stale ({'none' if a is None else f'{a:.0f} min'}, limit 10 min) — entries closed"})
    for k, a in accounts.items():
        if a.get("leverage") is not None and a["leverage"] > L.LEVERAGE_MAX:
            out.append({"kind": "leverage", "account": k, "ru": f"Счёт «{a['name']}»: плечо {a['leverage']:.2f}× > 3× — входы, которые его увеличат, закрыты",
                        "en": f"Account «{k}»: leverage {a['leverage']:.2f}× > 3× — entries that add to it are blocked"})
    denom = max(comb["gross"], comb["equity"]) or 0.0
    for s, c in sorted(coins.items()):
        if denom and c["gross"] / denom > L.COIN_MAX:
            out.append({"kind": "coin", "coin": s, "ru": f"{s}: {c['gross'] / denom * 100:.0f}% брутто > 25% — входы в монету закрыты",
                        "en": f"{s}: {c['gross'] / denom * 100:.0f}% of gross > 25% — entries in this coin are blocked"})
    for c in cls:
        if len(c["members"]) > 1 and denom and abs(c["net"]) / denom > L.CLUSTER_MAX:
            m = ", ".join(c["members"][:5])
            out.append({"kind": "cluster", "members": c["members"], "ru": f"Кластер {m}: {abs(c['net']) / denom * 100:.0f}% брутто > 50% — входы в ту же сторону закрыты",
                        "en": f"Cluster {m}: {abs(c['net']) / denom * 100:.0f}% of gross > 50% — entries on the same side are blocked"})
    for k, v in sorted((conveyor or {}).items()):
        out.append({"kind": "conveyor", "desk": k, "ru": v, "en": "Strategy conveyor: desk paused or retired — no new entries"})
    return out


def _conveyor(base: str | None) -> dict:
    doc, _err = G.read_json(L.path(L.CONVEYOR_FILE, base))
    out = {}
    try:
        for k in ((doc or {}).get("desks") or {}):
            why = G.conveyor_block(k, base)
            if why:
                out[k] = why
    except AttributeError:
        pass
    return out


def _skips(agents_db: str | None, now: int) -> dict:
    out = {"today": 0, "recent": []}
    if not agents_db or not os.path.exists(agents_db):
        return out
    try:
        c = sqlite3.connect(f"file:{agents_db}?mode=ro", uri=True, timeout=5)
        try:
            day0 = now // DAY * DAY
            out["today"] = c.execute("SELECT COUNT(*) FROM risk_skips WHERE t >= ?", (day0,)).fetchone()[0]
            out["recent"] = [{"t": t, "desk": d, "symbol": s, "side": sd, "notional_usd": n, "reason_ru": r} for d, t, s, sd, n, r in
                             c.execute("SELECT desk, t, symbol, side, notional, reason FROM risk_skips ORDER BY t DESC LIMIT 20")]
        finally:
            c.close()
    except sqlite3.Error:
        pass
    return out


# ───────────────────────────────────────────────────────────────────────────────────────────── the step
def compute(paper: dict, prev: dict, now: int, *, base: str | None = None, agents_db: str | None = None,
            candles_db: str | None = None) -> dict:
    accounts = accounts_from_paper(paper)
    if not accounts:
        raise ValueError("в paper.json нет ни одного счёта")
    coins = coins_of(accounts)
    comb = {"equity": round(sum(a["equity"] for a in accounts.values()), 2), "gross": round(sum(a["gross"] for a in accounts.values()), 2),
            "net": round(sum(a["net"] for a in accounts.values()), 2), "accounts": len(accounts)}
    comb["leverage"] = round(comb["gross"] / comb["equity"], 4) if comb["equity"] > 0 else None
    warnings: list = []
    # correlation: every coin with exposure + every coin with daily closes (so the gate can place a new coin in its cluster)
    cls: list = []
    try:
        closes = daily_closes(agents_db, candles_db, set(coins), now)
        rets = {s: r for s, r in ((s, returns(c, now)) for s, c in closes.items()) if len(r) >= 20}
        weight = {s: c["gross"] for s, c in coins.items()}
        for mem in clusters(rets, weight):
            cls.append({"members": mem, "gross": round(sum(coins.get(m, {}).get("gross", 0.0) for m in mem), 2),
                        "net": round(sum(coins.get(m, {}).get("net", 0.0) for m in mem), 2)})
        no_hist = sorted(s for s in coins if s not in rets)
        if no_hist:
            warnings.append(f"нет 30 дней истории для корреляции: {', '.join(no_hist[:10])} — каждая монета сама себе кластер")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"корреляция не посчитана: {type(e).__name__}: {str(e)[:100]}")
    cls.sort(key=lambda c: (-abs(c["net"]), c["members"][0]))
    for i, c in enumerate(cls):
        c["id"] = i
    cluster_of = {m: c["id"] for c in cls if len(c["members"]) > 1 for m in c["members"]}
    data = data_freshness(candles_db, now)
    ix, notes = update_index(prev, accounts, now)
    warnings += notes
    # latches: the daily stop holds until the next UTC day; the drawdown stop until the owner resets it (risk reset)
    latch = dict(prev.get("latch") or {})
    events = list(prev.get("events") or [])
    if latch.get("daily") and latch["daily"].get("day") != G.utc_day(now):
        G.log_event("daily_halt_released", {"day": latch["daily"].get("day")}, base, now)
        events.append({"t": now, "ru": "Новый день UTC — дневной стоп снят", "en": "New UTC day — daily stop released"})
        latch["daily"] = None
    last_reset = int(prev.get("last_reset_t") or 0)
    req, _ = G.read_json(L.path(L.RESET_FILE, base))
    if req and int(req.get("t") or 0) > last_reset:
        last_reset = int(req["t"])
        if latch.get("drawdown"):
            G.log_event("drawdown_reset", {"reason": req.get("reason") or "", "by": req.get("by"), "dd": ix["drawdown"]}, base, now)
            events.append({"t": now, "ru": "Владелец сбросил стоп по просадке — пик пересчитан от текущего уровня",
                           "en": "The owner reset the drawdown stop — the peak restarts from the current level"})
        latch["drawdown"] = None
        ix["peak"] = ix["value"]
        ix["drawdown"] = 0.0
    if ix["daily"] <= L.DAILY_LOSS_HALT and not latch.get("daily"):
        latch["daily"] = {"day": G.utc_day(now), "since": now, "value": round(ix["daily"], 5)}
        G.log_event("daily_halt", {"daily": ix["daily"], "day_pnl_usd": ix["day_pnl_usd"]}, base, now)
        events.append({"t": now, "ru": f"Дневной стоп: все демо-счета за день {ix['daily'] * 100:+.2f}% — новые входы закрыты до 00:00 UTC",
                       "en": f"Daily stop: all demo accounts {ix['daily'] * 100:+.2f}% today — no new entries until 00:00 UTC"})
    if ix["drawdown"] <= L.DRAWDOWN_HALT and not latch.get("drawdown"):
        latch["drawdown"] = {"since": now, "value": round(ix["drawdown"], 5)}
        G.log_event("drawdown_halt", {"drawdown": ix["drawdown"]}, base, now)
        events.append({"t": now, "ru": f"Стоп по просадке: {ix['drawdown'] * 100:+.2f}% от пика — новые входы закрыты до сброса владельцем",
                       "en": f"Drawdown stop: {ix['drawdown'] * 100:+.2f}% from peak — no new entries until the owner resets it"})
    kill = G.kill_status(base)
    conv = _conveyor(base)
    rows = limit_rows(ix, accounts, coins, cls, data, comb)
    blocks = blocks_of(rows, accounts, coins, cls, comb, latch, kill, conv, data)
    halt = any(b["kind"] in ("kill", "daily_halt", "drawdown_halt", "stale_data") for b in blocks)
    state = "HALT" if halt else "WARN" if blocks or any(r["status"] != "ok" for r in rows) else "OK"
    if state != prev.get("state"):
        G.log_event("state", {"from": prev.get("state"), "to": state, "blocks": [b["kind"] for b in blocks]}, base, now)
        if prev.get("state"):
            events.append({"t": now, "ru": f"Риск-слой: {prev.get('state')} → {state}", "en": f"Risk layer: {prev.get('state')} → {state}"})
    return {"version": 1, "updated_at": now, "state": state, "error": None, "warnings": warnings[:20],
            "accounts": {k: {kk: vv for kk, vv in a.items() if kk != "positions"} | {"positions": a["positions"][:60]} for k, a in accounts.items()},
            "combined": comb, "coins": coins, "clusters": cls, "cluster_of": cluster_of, "data": data, "index": ix, "latch": latch,
            "last_reset_t": last_reset, "kill": {k: v for k, v in kill.items() if k != "history"}, "conveyor": conv,
            "limits": rows, "blocks": blocks, "events": events[-30:], "skips": _skips(agents_db, now),
            "prev_accounts": {k: {"mtm": a["equity"], "since": a.get("since")} for k, a in accounts.items()}}


NOT_COVERED_RU = ["Столы GitHub-конвейера (фандинг, базис, доходность, мемы, киты, спот-портфели, распределитель) — считаются раз в "
                  "~30 минут на GitHub и в этот подсчёт не входят",
                  "Зеркало на демо-бирже (Bybit) и реальные деньги — вне этого слоя",
                  "Лимитные заявки, ещё не исполненные, в брутто не входят (проверяются при исполнении)",
                  "Когда сервер молчит и столы берёт GitHub, выключатель и лимиты сервера там не видны"]
NOT_COVERED_EN = ["GitHub pipeline desks (funding, basis, yield, memes, whales, spot portfolios, allocator) run every ~30 min on "
                  "GitHub and are not in this count",
                  "The exchange demo mirror (Bybit) and real money are outside this layer",
                  "Unfilled limit orders are not in gross (they are checked when they fill)",
                  "When the server is down and GitHub runs the desks, the server's kill switch and limits are not seen there"]


def public(st: dict) -> dict:
    """web/risk.json: what anyone may see — no free-text reasons of the owner, no holdings outside the demo desks"""
    comb = st.get("combined") or {}
    denom = max(comb.get("gross") or 0, comb.get("equity") or 0) or 0
    ix = st.get("index") or {}
    lim = [{"key": r["key"], "name": r["name"], "value": r["value"], "limit": r["limit"], "unit": r["unit"], "pct_of_limit":
            None if r["pct"] is None else round(r["pct"] * 100, 1), "status": r["status"], "action": r["action"], "text": _fmt(r),
            **{k: r[k] for k in ("account", "coin", "members") if r.get(k) is not None}} for r in st.get("limits") or []]
    head = {"OK": ("Риск в норме: все лимиты ниже 70%", "Risk OK: every limit below 70%"),
            "WARN": ("Внимание: лимит близко или часть входов закрыта", "Warning: a limit is close or some entries are blocked"),
            "HALT": ("СТОП: новые входы закрыты", "HALT: no new entries")}[st.get("state") or "WARN"]
    k = st.get("kill") or {}
    return {"asof": st.get("updated_at"), "updated_at": st.get("updated_at"), "state": st.get("state"),
            "headline": {"ru": head[0], "en": head[1]}, "error": st.get("error"),
            "kill_switch": {"on": bool(k.get("on")), "since": k.get("since") if k.get("on") else None},
            "limits": lim, "blocks": [{kk: vv for kk, vv in b.items()} for b in st.get("blocks") or []],
            "combined": {"accounts": comb.get("accounts"), "equity": comb.get("equity"), "gross": comb.get("gross"), "net": comb.get("net"),
                         "leverage": comb.get("leverage"), "day_pnl_usd": ix.get("day_pnl_usd"),
                         "daily_pct": None if ix.get("daily") is None else round(ix["daily"] * 100, 3),
                         "drawdown_pct": None if ix.get("drawdown") is None else round(ix["drawdown"] * 100, 3),
                         "index": None if ix.get("value") is None else round(ix["value"], 6), "since": ix.get("started")},
            "accounts": [{"key": kk, "name": a.get("name"), "equity": a.get("equity"), "gross": a.get("gross"), "net": a.get("net"),
                          "leverage": a.get("leverage"), "open": a.get("n_open"), "mirror_of": a.get("mirror_of")}
                         for kk, a in sorted((st.get("accounts") or {}).items(), key=lambda kv: -(kv[1].get("leverage") or 0))],
            "top_coins": [{"coin": s, "gross": c["gross"], "net": c["net"], "share": round(c["gross"] / denom, 4) if denom else None}
                          for s, c in sorted((st.get("coins") or {}).items(), key=lambda kv: -kv[1]["gross"])[:8]],
            "clusters": [{"members": c["members"], "net": c["net"], "gross": c["gross"],
                          "share": round(abs(c["net"]) / denom, 4) if denom else None}
                         for c in (st.get("clusters") or []) if len(c["members"]) > 1 and (c["gross"] or 0) > 0][:5],
            "data": {"age_min": (st.get("data") or {}).get("age_min"), "source": (st.get("data") or {}).get("source")},
            "skips": st.get("skips") or {"today": 0, "recent": []}, "events": (st.get("events") or [])[-10:],
            "conveyor_paused": sorted((st.get("conveyor") or {}).keys()),
            "rules": {"ru": "СТОП: день ≤ −3% (до 00:00 UTC), просадка от пика ≤ −15% (до сброса владельцем), данные старше 10 мин, "
                            "аварийный выключатель. Блок входа: плечо счёта > 3×, монета > 25% брутто, кластер похожих монет "
                            "(ρ > 0.8 за 30 дней) > 50% брутто по чистой позиции. Предупреждение — с 70% лимита. Выходы (стопы, цели) "
                            "работают всегда. Брутто для долей — не меньше общего капитала счетов.",
                      "en": "HALT: day ≤ −3% (until 00:00 UTC), drawdown from peak ≤ −15% (until the owner resets), data older "
                            "than 10 min, kill switch. Entry blocked: account leverage > 3×, one coin > 25% of gross, a cluster of "
                            "similar coins (ρ > 0.8 over 30 days) > 50% of gross by net exposure. Warning from 70% of a limit. Exits "
                            "(stops, targets) always work. Gross for the shares is at least the accounts' total equity."},
            "not_covered": {"ru": NOT_COVERED_RU, "en": NOT_COVERED_EN},
            "about": {"ru": "Портфельный риск-слой демо-счетов CRYPTO NOVΛ AI: проверяется каждую минуту перед каждым новым входом. "
                            "Только демо — реальные деньги здесь не участвуют.",
                      "en": "Portfolio risk layer of the CRYPTO NOVΛ AI demo accounts: checked every minute before every new entry. "
                            "Demo only — no real money here."}}


def run(out: str = "web", now: int | None = None, *, base: str | None = None, agents_db: str | None = None,
        candles_db: str | None = None) -> tuple[dict, str | None]:
    """the engine step → (state, error). Never raises."""
    now = now or int(time.time() * 1000)
    base = base or L.risk_dir()
    agents_db = agents_db if agents_db is not None else os.path.join("data", "agents.sqlite")
    if candles_db is None:
        candles_db = os.environ.get("CNQ_DB", os.path.join("data", "cnq.sqlite"))
    sp = L.path(L.STATE_FILE, base)
    prev, perr = G.read_json(sp)
    prev = prev if isinstance(prev, dict) else {}
    err = None
    try:
        with open(os.path.join(out, "paper.json"), encoding="utf-8") as f:
            paper = json.load(f)
        st = compute(paper, prev, now, base=base, agents_db=agents_db, candles_db=candles_db)
        if perr:
            st["warnings"].insert(0, f"прошлое состояние не читалось ({perr[:80]}) — индекс начат заново")
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:200]}"
        st = dict(prev) if prev else {"version": 1, "updated_at": None, "state": "HALT", "limits": [], "blocks": [], "accounts": {},
                                      "combined": {}, "index": {}, "latch": {}, "events": []}
        st["error"], st["error_at"] = err, now
        st["kill"] = {k: v for k, v in G.kill_status(base).items() if k != "history"}
    for p, doc in ((sp, st), (os.path.join(out, "risk.json"), public(st))):
        try:
            G.write_json(p, doc)
        except Exception as e:  # noqa: BLE001
            err = (err + " | " if err else "") + f"write {os.path.basename(p)}: {e}"
    return st, err

"""Owner's Futures picture (06.11.2026, owner 17:30: «понятная картина по фьючерсам … от кого что мы получаем») → web/fut_owner.json.

Read-only: it joins the files the system already publishes and changes nothing.
  • the real OKX futures since 22.10 (web/public_real.json: fut_curve, alloc, closed, futures, actions);
  • which whale each REAL trade copied: the trade's signal id (open: futures[].sig; closed: web/real_why.json closed[].sig by
    coin + open time, else the fut_open action of the same coin within 10 minutes) → the whale desk's trade with the same coin, side
    and open time (web/whales_live.json desk open/closed, web/whales_top.json desk.paper.open) → the trader and his entry;
    real_why.json's memory is the fallback;
  • the followed traders (whales_top.json following + follow_lock), the dropped ones, the bench;
  • the demo desk «Киты» for comparison, the tests (strategies.json; «CRYPTO NOVA v1 (правила, до 04.11)» = the rule desks the
    real futures followed until 04.11) and «Иван тест» (ivan_stats.json — the detailed trades stay in the encrypted file).
Internal: the whale names are shown only in the owner's views of the panel (owner 05.11 09:52). Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=2))

SWITCH_T = int(dt.datetime(2026, 11, 4, 0, 0, tzinfo=KYIV).timestamp() * 1000)     # owner 04.11: real futures → whale copies
FIX_T = int(dt.datetime(2026, 11, 6, 12, 41, tzinfo=KYIV).timestamp() * 1000)      # owner 06.11 12:41: execution fixes
BUDGET_T = int(dt.datetime(2026, 11, 5, 14, 21, tzinfo=KYIV).timestamp() * 1000)   # futures share 15 % → 25 % (public_real alloc)
MATCH_MS = 10 * 60_000
TIME_MATCH_MS = 60 * 60_000                 # a copy opens within an hour of the whale desk's trade (poll + execution lag)
CURVE_N = 320


def _load(out: str, name: str) -> dict:
    try:
        d = json.load(open(os.path.join(out, name), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _usd_ru(v) -> str:
    if v is None:
        return "—"
    a = abs(float(v))
    body = (f"{a:,.2f}" if a < 10 else f"{a:,.0f}").replace(",", " ").replace(".", ",")
    return ("−" if v < 0 else "+") + "$" + body


def _pct_ru(v) -> str:
    return "—" if v is None else (("−" if v < 0 else "+") + f"{abs(float(v)):.1f}".replace(".", ",") + "%")


def _coin(inst) -> str:
    return re.sub(r"^1000", "", str(inst or "").split("-")[0]).upper()


def _sig_parts(sig):
    m = re.match(r"whales:W-([A-Za-z0-9]+)-([LS])-(\d+)", str(sig or ""))
    return (re.sub(r"^1000", "", m.group(1)).upper(), "LONG" if m.group(2) == "L" else "SHORT", int(m.group(3))) if m else None


def _at(curve: list, t: int):
    v = None
    for x, y in curve:
        if x <= t:
            v = y
        else:
            break
    return v


def _sample(points: list, n: int) -> list:
    if len(points) <= n:
        return points
    step = len(points) / (n - 1)
    out = [points[int(i * step)] for i in range(n - 1)] + [points[-1]]
    return out


def _stats(pnls: list[float]) -> dict:
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p < 0]
    gl = -sum(loss)
    return {"n": n, "wins": len(wins), "losses": len(loss), "win_pct": _r(len(wins) / n * 100, 1) if n else None,
            "pf": _r(sum(wins) / gl, 2) if gl > 0 else (None if not wins else 99.0), "sum_usd": _r(sum(pnls)),
            "avg_usd": _r(sum(pnls) / n) if n else None}


def desk_trades(wl: dict, wt: dict) -> dict:
    """(coin, side, opened_t) → the whale desk's trade {whale, whale_ru, whale_entry, entry, pnl, …}"""
    out = {}
    names = wl.get("names") or {}
    for d in (wl.get("desk") or {}).get("closed") or [], (wl.get("desk") or {}).get("open") or [], (wt.get("desk") or {}).get("open") or [], \
            (wt.get("desk") or {}).get("closed") or []:
        for x in d:
            if not isinstance(x, dict) or not x.get("opened_t"):
                continue
            k = (_coin(x.get("coin")), str(x.get("side") or "").upper(), int(x["opened_t"]))
            out.setdefault(k, {**x, "whale_ru": x.get("whale_ru") or names.get(x.get("whale"))})
    for x in ((wt.get("desk") or {}).get("paper") or {}).get("open") or []:
        p = _sig_parts("whales:" + str(x.get("id") or ""))
        if p and p not in out:
            out[p] = {"whale": x.get("whale"), "whale_entry": x.get("whale_entry"), "entry": x.get("entry"), "coin": p[0], "side": p[1],
                      "opened_t": p[2], "whale_ru": names.get(x.get("whale"))}
    return out


def real_trades(real: dict, rw: dict, desk: dict, names: dict) -> list[dict]:
    acts = [a for a in real.get("actions") or [] if isinstance(a, dict) and a.get("kind") == "fut_open" and a.get("sig")]
    rwc = [c for c in rw.get("closed") or [] if isinstance(c, dict)]
    memo = {**(rw.get("memory") or {}), **(rw.get("by_sig") or {})}

    def sig_for(coin, opened):
        for c in rwc:
            if _coin(c.get("inst")) == coin and c.get("sig") and isinstance(c.get("opened"), (int, float)) and opened \
                    and abs(c["opened"] - opened) <= MATCH_MS:
                return c["sig"]
        best = None
        for a in acts:
            if str(a.get("sym") or "").upper() == coin and opened and abs((a.get("t") or 0) - opened) <= MATCH_MS:
                best = a["sig"]
        return best

    rows = []
    items = [(c, True) for c in real.get("closed") or [] if isinstance(c, dict) and not c.get("partial")] + \
            [(o, False) for o in real.get("futures") or [] if isinstance(o, dict)]
    for x, closed in items:
        coin = _coin(x.get("inst"))
        side = "LONG" if str(x.get("side") or "long").lower() == "long" else "SHORT"
        sig = x.get("sig") or sig_for(coin, x.get("opened"))
        whale, wentry, wname = None, None, None
        p = _sig_parts(sig)
        if p:
            d = desk.get(p) or {}
            whale, wentry, wname = d.get("whale"), d.get("whale_entry"), d.get("whale_ru")
            if not whale:
                w = (memo.get(sig) or {}).get("whale") or {}
                whale, wentry, wname = w.get("addr"), w.get("entry"), w.get("name")
        match = "sig" if p else None
        if not p and (x.get("opened") or 0) >= SWITCH_T:      # no signal id recorded: the whale desk's trade of the same coin and side
            op = int(x.get("opened") or 0)                     # opened up to 60 min before our entry (the copy's lag), closest first
            cand = sorted(((op - k[2], v) for k, v in desk.items() if k[0] == coin and k[1] == side and -2 * 60_000 <= op - k[2] <= TIME_MATCH_MS),
                          key=lambda z: abs(z[0]))
            if cand:
                d = cand[0][1]
                whale, wentry, wname, p, match = d.get("whale"), d.get("whale_entry"), d.get("whale_ru"), True, "time"
        strat = "whales" if p else ("v1" if sig else ("v1" if (x.get("opened") or 0) < SWITCH_T else "unknown"))
        entry = x.get("open_px") if closed else x.get("entry")
        gap = None
        if entry and wentry:
            gap = (float(entry) / float(wentry) - 1) * 100 * (1 if side == "LONG" else -1)     # + = we entered worse than the trader
        rows.append({"coin": coin, "side": side, "closed": closed, "opened": x.get("opened"), "closed_t": x.get("closed") if closed else None,
                     "entry": entry, "exit": x.get("close_px") if closed else None, "mark": None if closed else x.get("mark"),
                     "stop": None if closed else x.get("stop"), "lev": x.get("lev"),
                     "pnl_usd": _r(x.get("pnl_usd") if closed else x.get("upnl_usd")), "move_pct": _r(x.get("move_pct") if closed else x.get("upnl_pct")),
                     "sig": sig, "strategy": strat, "whale": whale, "whale_name": wname or names.get(whale or ""),
                     "whale_entry": wentry, "entry_gap_pct": _r(gap, 2), "match": match})
    rows.sort(key=lambda r: -(r.get("opened") or 0))
    return rows


def _trader(f: dict, copies: list, lock: dict, status: str) -> dict:
    cl = [c["pnl_usd"] or 0.0 for c in copies if c["closed"]]
    op = [c for c in copies if not c["closed"]]
    gaps = [c["entry_gap_pct"] for c in copies if c.get("entry_gap_pct") is not None]
    return {"addr": f.get("addr"), "name": f.get("name") or f.get("short") or f.get("addr"), "src": f.get("src"),
            "win_pct": _r((f.get("win") or 0) * 100, 1) if f.get("win") is not None else None, "pf": _r(f.get("pf"), 2),
            "decisions": f.get("decisions"), "hold_med_h": _r(f.get("hold_med_h"), 1), "pnl_60d": _r(f.get("pnl_60d"), 0),
            "lev_med": f.get("lev_med"), "status": status, "joined": (lock.get("joined") or {}).get(f.get("addr")),
            "why_ru": f.get("why_ru"),
            "copies": copies, "totals": {**_stats(cl), "open_n": len(op), "open_usd": _r(sum(c["pnl_usd"] or 0 for c in op)),
                                         "total_usd": _r(sum(cl) + sum(c["pnl_usd"] or 0 for c in op)),
                                         "avg_gap_pct": _r(sum(gaps) / len(gaps), 2) if gaps else None}}


def build(out: str = "web", now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    real, rw, wt, wl = (_load(out, n) for n in ("public_real.json", "real_why.json", "whales_top.json", "whales_live.json"))
    names = {**(wl.get("names") or {})}
    for f in (wt.get("following") or []) + (wt.get("follow_dropped") or []) + (wt.get("bench") or []) + (wt.get("top") or []):
        if isinstance(f, dict) and f.get("addr") and f.get("name"):
            names.setdefault(f["addr"], f["name"])
    since = int(real.get("since") or SWITCH_T)
    A = real.get("alloc") or {}
    fc = sorted((int(p[0]), float(p[1])) for p in real.get("fut_curve") or [] if isinstance(p, list) and len(p) == 2 and p[1] is not None)
    curve = ([(since, 0.0)] if not fc or fc[0][0] > since else []) + fc
    fut_now = float(real.get("fut_pnl_usd")) if real.get("fut_pnl_usd") is not None else (curve[-1][1] if curve else 0.0)
    if curve and curve[-1][0] < now:
        curve.append((min(now, int(real.get("asof") or now)), fut_now))
    at_switch = _at(curve, SWITCH_T) or 0.0
    day0 = int(dt.datetime.fromtimestamp(now / 1000, KYIV).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    at_day = _at(curve, day0)
    start_usd = float(real.get("start_usd") or 0)
    trades = real_trades(real, rw, desk_trades(wl, wt), names)
    closed = [t for t in trades if t["closed"]]
    st = _stats([t["pnl_usd"] or 0.0 for t in closed])
    best = max(closed, key=lambda t: t["pnl_usd"] or 0, default=None)
    worst = min(closed, key=lambda t: t["pnl_usd"] or 0, default=None)
    kpi = {"budget_usd": _r(A.get("fut_budget_usd")), "budget_pct": A.get("fut_pct"), "budget_start_usd": _r(start_usd * 0.15),
           "budget_start_pct": 15.0, "budget_changed_t": BUDGET_T,
           "pnl_all_usd": _r(fut_now), "pnl_switch_usd": _r(fut_now - at_switch),
           "margin_usd": _r(A.get("fut_margin_usd")), "reserve_usd": _r(A.get("fut_reserve_usd")),
           "used_pct": _r(float(A.get("fut_margin_usd") or 0) / float(A.get("fut_budget_usd")) * 100, 1) if A.get("fut_budget_usd") else None,
           "open_n": sum(1 for t in trades if not t["closed"]), "open_usd": _r(sum(t["pnl_usd"] or 0 for t in trades if not t["closed"])),
           "closed": {**st, "best": {k: best[k] for k in ("coin", "side", "pnl_usd")} if best else None,
                      "worst": {k: worst[k] for k in ("coin", "side", "pnl_usd")} if worst else None},
           "closed_switch": _stats([t["pnl_usd"] or 0.0 for t in closed if (t.get("opened") or 0) >= SWITCH_T]),
           "today_usd": _r(fut_now - at_day) if at_day is not None else None}
    # traders
    lock = wt.get("follow_lock") or {}
    by_w: dict[str, list] = {}
    for t in trades:
        if t["strategy"] == "whales" and t.get("whale"):
            by_w.setdefault(t["whale"], []).append(t)
    fol = [f for f in wt.get("following") or [] if isinstance(f, dict)]
    following = [_trader(f, by_w.get(f["addr"], []), lock, "following") for f in fol]
    seen = {f["addr"] for f in fol}
    dropped = []
    for f in wt.get("follow_dropped") or []:
        if isinstance(f, dict) and f.get("addr") and f["addr"] not in seen:
            seen.add(f["addr"])
            dropped.append(_trader({**f, "why_ru": "; ".join(f.get("why_ru") or []) if isinstance(f.get("why_ru"), list) else f.get("why_ru")},
                                   by_w.get(f["addr"], []), lock, "dropped"))
    for addr, cs in by_w.items():                       # copied, but no longer on any list
        if addr not in seen:
            seen.add(addr)
            dropped.append(_trader({"addr": addr, "name": names.get(addr) or cs[0].get("whale_name") or addr}, cs, lock, "dropped"))
    dropped.sort(key=lambda x: -(len(x["copies"])))
    bench = [{"addr": b.get("addr"), "name": b.get("name"), "src": b.get("src"), "win_pct": _r((b.get("win") or 0) * 100, 1),
              "pf": _r(b.get("pf"), 2), "decisions": b.get("decisions"), "pnl_60d": _r(b.get("pnl_60d"), 0), "why_ru": b.get("why_ru")}
             for b in wt.get("bench") or [] if isinstance(b, dict)]
    unattr = [t for t in trades if (t["strategy"] == "whales" and not t.get("whale")) or t["strategy"] == "unknown"]
    v1 = [t for t in trades if t["strategy"] == "v1"]
    # the demo desk «Киты» and the copy quality
    D = dict(wt.get("desk") or {})
    X = wl.get("desk") or {}
    if X.get("equity") is not None and (wl.get("asof") or 0) > (wt.get("asof") or 0):
        # 10.11 (owner): the server's live desk (whales_live.json, on every change) beats the pipeline's 30-minute snapshot
        xc = [c for c in X.get("closed") or [] if isinstance(c, dict)]
        st_ = float(X.get("start") or D.get("start") or 10_000.0)
        D.update(start=st_, equity=float(X["equity"]), pnl_usd=float(X["equity"]) - st_, pnl_pct=(float(X["equity"]) / st_ - 1) * 100,
                 trades=len(xc), wins=sum(1 for c in xc if float(c.get("pnl") or 0) > 0), started=X.get("started") or D.get("started"),
                 history=X.get("history") or D.get("history"))
    dcl = [c for c in X.get("closed") or D.get("closed") or [] if isinstance(c, dict)]
    d_before = sum(float(c.get("pnl") or 0) for c in dcl if (c.get("closed_t") or 0) < SWITCH_T)
    d_start = float(D.get("start") or 10_000.0)
    d_eq = float(D.get("equity") or d_start)
    d_switch_pct = (d_eq / (d_start + d_before) - 1) * 100 if d_start + d_before > 0 else None
    budget_switch = start_usd * 0.15 + at_switch
    r_switch_pct = (fut_now - at_switch) / budget_switch * 100 if budget_switch > 0 else None
    gaps = [t["entry_gap_pct"] for t in trades if t.get("entry_gap_pct") is not None]
    desk = {"name_ru": "Киты · демо-стол $10 000", "start": d_start, "equity": _r(d_eq), "pnl_usd": _r(D.get("pnl_usd")),
            "pnl_pct": _r(D.get("pnl_pct")), "started": D.get("started"), "trades": D.get("trades"), "wins": D.get("wins"),
            "max_dd_pct": D.get("max_dd_pct"), "switch_pct": _r(d_switch_pct), "real_switch_pct": _r(r_switch_pct),
            "avg_entry_gap_pct": _r(sum(gaps) / len(gaps), 2) if gaps else None, "gap_n": len(gaps),
            "history": [[int(t_), _r(v)] for t_, v in _sample([p for p in D.get("history") or [] if isinstance(p, list)], 160)]}
    # tests
    S = _load(out, "strategies.json")
    tests = []
    v1_row = None
    try:
        from . import app_home
        p = _load(out, "paper.json")
        usd, _c = app_home.nova_result(p.get("profiles") or {}, now, _load(out, "desk_select.json") or None)
        v1_row = {"key": "v1", "name_ru": "CRYPTO NOVA v1 (правила, до 04.11)", "status_ru": "демо, продолжает работать",
                  "net_usd": _r(usd), "trades": sum(int(((p.get("profiles") or {}).get(k) or {}).get("stats", {}).get("trades") or 0) for k in ("aggr", "imp")),
                  "about_ru": "Правила системы (откупы падений + «Импульс BTC») — вели реальные фьючерсы до 04.11; дальше только демо."}
    except Exception:  # noqa: BLE001
        v1_row = {"key": "v1", "name_ru": "CRYPTO NOVA v1 (правила, до 04.11)", "status_ru": "демо, продолжает работать"}
    tests.append(v1_row)
    tests.append({"key": "v2", "name_ru": "CRYPTO NOVA v2 — копирование китов, с 04.11", "status_ru": "реальный счёт + демо-стол",
                  "net_usd": desk["pnl_usd"], "net_pct": desk["pnl_pct"], "trades": desk["trades"],
                  "about_ru": f"Реальные фьючерсы с 04.11: {_usd_ru(fut_now - at_switch)}; демо-стол {_pct_ru(desk['pnl_pct'])} с 18.10."})
    for x in S.get("strategies") or []:
        if not isinstance(x, dict) or x.get("key") in ("whales_copy", "aggr", "imp") or x.get("status") == "In Development":
            continue
        tests.append({"key": x.get("key"), "name_ru": x.get("name_ru"), "status_ru": x.get("status_ru"), "net_usd": x.get("net_usd"),
                      "net_pct": x.get("net_pct"), "trades": x.get("trades"), "real": bool(x.get("real_money"))})
    # Ivan
    iv = _load(out, "ivan_stats.json")
    ivan = {"stats": iv or None, "copied_real": False,
            "source_ru": "Bybit (демо-счёт платформы Ивана), ключ только для чтения; сервер читает счёт каждые 15 минут "
                         "(таймер cnq-okx-monitors) и шифрует подробности в web/ivan.enc.json.",
            "copy_ru": "Реальный счёт Ивана сейчас не копирует: в исполнителе фьючерсов нет источника «Иван»."}
    return {"asof": now, "since": since, "switch_t": SWITCH_T, "fix_t": FIX_T,
            "markers": [{"t": SWITCH_T, "ru": "04.11 · копирование китов"}, {"t": BUDGET_T, "ru": "05.11 · бюджет 15% → 25%"},
                        {"t": FIX_T, "ru": "06.11 12:41 · исправления"}],
            "curve": [[t_, _r(v)] for t_, v in _sample(curve, CURVE_N)], "kpi": kpi,
            "following": following, "dropped": dropped, "bench": bench,
            "lock": {"t": lock.get("t"), "next_t": lock.get("next_t"), "ru": lock.get("ru")},
            "unattributed": {"n": len(unattr), "sum_usd": _r(sum(t["pnl_usd"] or 0 for t in unattr)),
                             "ru": "сделки после 04.11 без записанного сигнала и без сделки стола рядом по времени — источник не установлен"},
            "v1_trades": {"n": len(v1), "sum_usd": _r(sum(t["pnl_usd"] or 0 for t in v1 if t["closed"]))},
            "trades": trades, "desk": desk, "tests": tests, "ivan": ivan,
            "about_ru": "Только для владельца: фьючерсы реального счёта OKX с 22.10, от кого из трейдеров что получено, демо-стол и тесты."}


def run(out: str = "web", now: int | None = None) -> dict:
    doc = build(out, now)
    with open(os.path.join(out, "fut_owner.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    return doc

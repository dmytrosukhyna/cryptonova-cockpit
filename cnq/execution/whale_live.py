"""«Киты» live — the copy desk's eyes on the server, every pass of the real futures sleeve (04.11.2026 22:35, owner: «Фьючерсы должны
моментально копироваться»).

Before: the GitHub pipeline looked at the followed whales every ~10–30 minutes (cnq/agents/whale_study.run → web/whales_top.json →
desk.paper) and the real OKX sleeve (okx_futures) copied what it found there — a whale's new position reached our account 10–60
minutes late, and short-hold traders could not be copied at all.

Now: WHO to follow stays the GitHub study's decision (whales_top.json «following», with each whale's record and signals a day);
WHAT they hold is read here, inside okx_futures.step, i.e. every ~60 s in the autopilot loop (server service or the Mac window —
whichever holds the account's lease). Each pass: Hyperliquid mids (one call) + the current positions of every followed whale and of
every whale we still hold a copy of (Hyperliquid clearinghouseState, OKX public lead-trader sub-positions, Binance lead-portfolio
positions; ~0.25 s apart, far inside each source's public rate limit) → whale_study.desk_step — the SAME rules as before (new
positions and joins inside −3 %…+1 % of the whale's entry, stops, owner overrides, 5× cap; sizing by free margin ÷ the copies still
expected today, ≤ 50 % per whale) → the desk in the paper-desk shape that okx_futures follows. A whale's new position or close is
seen within one pass (≈ 1 minute) and mirrored in the same pass.

State: OKX_WHALE_LIVE_STATE (default ~/.cryptonova_whale_live.json). The first pass starts from the published desk (whales_top.json
«desk»: the same copies with the same ids, so positions already held on OKX are kept) and only takes a baseline of the whales'
positions. A poll that fails changes nothing for that whale (desk_step). Every PUB_MS when something changed (at least every
PUB_IDLE_MS) the desk goes to web/whales_live.json through the GitHub contents API (CNQ_GH_TOKEN) — the GitHub study then publishes
THIS desk in whales_top.json instead of running its own, so the panel, desk_select and the real account show one desk.
Off with OKX_WHALE_LIVE=off (→ the old path: desk.paper from whales_top.json). Stdlib only."""
from __future__ import annotations

import base64
import json
import os
import time

from ..agents import whale_sources as WS
from ..agents import whale_study as W
from . import owner_overrides as ov

FOLLOW_STALE_MS = 6 * 3_600_000      # a follow list older than this → no new copies (closes and stops still work)
PUB_MS, PUB_IDLE_MS = 2 * 60_000, 10 * 60_000
POLL_GAP_S = 0.25
POLL_WORKERS = 8
GONE_MS = 30 * 60_000                # 05.11: a lead trader OKX answers «60004 Trader doesn't exist» for this long → treated as flat
GONE_MARKS = ("60004", "doesn't exist", "does not exist")
PATH = "web/whales_live.json"
REPO = "dmytrosukhyna/cryptonova-cockpit"


def enabled(env=None) -> bool:
    e = os.environ if env is None else env
    return (e.get("OKX_WHALE_LIVE") or "on").strip().lower() not in ("off", "no", "0", "false")


def state_path(env=None) -> str:
    e = os.environ if env is None else env
    return e.get("OKX_WHALE_LIVE_STATE") or os.path.join(os.path.expanduser("~"), ".cryptonova_whale_live.json")


def load(env=None) -> dict:
    try:
        with open(state_path(env), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save(st: dict, env=None) -> None:
    p = state_path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)


def seed(wtop: dict, now: int) -> dict:
    """the published desk (whales_top.json «desk») → a desk_step state with the same copies (same ids)"""
    return W.desk_from_public(wtop, now)


def src_of(addr: str) -> str:
    if str(addr).startswith("ivan:"):
        return "ivan"
    return WS.src_of(addr) or ("okx" if str(addr).startswith("okx:") else "hl")


# 06.11 17:33 (owner, real money, «по ивану да»): Ivan's Bybit account (the «Иван тест» read-only key IVAN_BYBIT_KEY / IVAN_BYBIT_SECRET,
# the same env file as this autopilot) is one more followed «whale», a fixed owner pick outside the 3-day lock (whales_top.json
# «following» with owner_pick). His positions are read directly from Bybit every pass (/v5/position/list, GET only) — not from the
# 15-minute encrypted web/ivan.enc.json.
IVAN_ADDR = "ivan:bybit"


def ivan_keys(env=None):
    e = os.environ if env is None else env
    k, sec = (e.get("IVAN_BYBIT_KEY") or "").strip(), (e.get("IVAN_BYBIT_SECRET") or "").strip()
    return (k, sec) if k and sec else None


def ivan_reader(env, st: dict):
    """→ a function () → Bybit position rows (size > 0), or None without the read-only key; the working host is remembered in st"""
    keys = ivan_keys(env)
    if not keys:
        return None
    from . import ivan_watch as IV
    fetch = IV.make_fetch(*keys)

    def read():
        host = st.get("ivan_host")
        if host not in IV.HOSTS:
            host, _ = IV.probe(fetch, None)
            st["ivan_host"] = host
        return IV.read_positions(fetch, IV.HOSTS[host])
    return read


def ivan_positions(rows: list) -> list[dict]:
    """Bybit /v5/position/list rows → [{coin, side, size, notional, entry, upnl, lev, sl, opened}] (coin without the USDT quote)"""
    out = []
    for x in rows or []:
        sz = W._f(x.get("size"))
        if not sz:
            continue
        sym = str(x.get("symbol") or "").upper()
        coin = sym[:-4] if sym.endswith("USDT") else sym
        side = "LONG" if str(x.get("side") or "").lower().startswith("b") else "SHORT"
        out.append({"coin": coin, "side": side, "size": sz, "notional": round(W._f(x.get("positionValue"))), "entry": W._f(x.get("avgPrice")),
                    "upnl": round(W._f(x.get("unrealisedPnl"))), "lev": W._f(x.get("leverage")) or None,
                    "sl": W._f(x.get("stopLoss")) or None, "opened": int(W._f(x.get("createdTime")))})
    return out


def poll(addr: str, mids: dict, post, okx_get, src_get: dict) -> tuple[list[dict], dict]:
    """one whale's current positions (Hyperliquid coin names) + {(coin, side): opened ms} where the source tells it"""
    k = src_of(addr)
    opened: dict = {}
    if k == "ivan":
        reader = src_get.get("ivan")
        if reader is None:
            raise RuntimeError("нет ключа IVAN_BYBIT_KEY / IVAN_BYBIT_SECRET — позиции Ивана не читаются")
        pos = ivan_positions(reader())
        for x in pos:
            if x.get("opened"):
                opened[(x["coin"], x["side"])] = x["opened"]
    elif k == "hl":
        pos = W.norm_positions(post({"type": "clearinghouseState", "user": addr}))
    elif k == "okx":
        cur = okx_get("/api/v5/copytrading/public-current-subpositions", {"instType": "SWAP", "uniqueCode": addr.split(":", 1)[1], "limit": 100})
        pos = W.okx_positions({"okx": cur})
        for t in W.okx_open_trips({"okx": cur}, int(time.time() * 1000)):
            if t.get("coin"):
                key = (t["coin"], t["side"])
                opened[key] = max(opened.get(key, 0), t["open_t"])
    else:
        sv = WS.SOURCES[k]
        cur = sv["current"](src_get.get(k) or sv["get"], {"addr": addr, "code": addr.split(":", 1)[1]})
        pos = sv["positions"](cur)
        for t in sv["open_trips"](cur, int(time.time() * 1000)):
            key = (t["coin"], t["side"])
            opened[key] = max(opened.get(key, 0), t["open_t"])
    out = []
    for x in pos:
        c = W.hl_name(x["coin"], mids) or x["coin"] if x.get("coin") else x.get("coin")
        out.append({**x, "coin": c})
    return out, {((W.hl_name(c, mids) or c), s): t for (c, s), t in opened.items()}


def step(wtop: dict | None, env=None, now: int | None = None, post=None, okx_get=None, src_get: dict | None = None,
         st: dict | None = None, persist: bool = True, put=None, sleep=time.sleep) -> dict | None:
    """one live pass → the desk in the paper-desk shape okx_futures follows ({name, equity, start_equity, open, closed, live});
    None when there is nothing to follow and nothing held"""
    now = int(now or time.time() * 1000)
    post, okx_get, src_get = post or W._post, okx_get or W._okx_get, src_get or {}
    st = st if st is not None else load(env)
    wtop = wtop if isinstance(wtop, dict) else {}
    desk = st.get("desk")
    # a recovery (04.11: a rolled-back whales_top.json was published and seeded here) — a published file carrying a new
    # «live_seed» token restarts the live desk from ITS desk once
    tok = wtop.get("live_seed")
    if tok and tok != st.get("seed_token") and wtop.get("desk"):
        desk = seed(wtop, now)
        st["seed_token"] = tok
    if not isinstance(desk, dict):
        if not wtop.get("desk") and not wtop.get("following"):
            return None
        desk = seed(wtop, now)
    fresh = now - int(W._f(wtop.get("asof"))) <= FOLLOW_STALE_MS
    fol = [x for x in (wtop.get("following") or []) if isinstance(x, dict) and x.get("addr")] if fresh else []
    follow = [x["addr"] for x in fol]
    quality = {x["addr"]: {k: x.get(k) for k in ("win", "pf", "per_day", "pnl_60d", "lev_med")} for x in fol}
    names = {**(st.get("names") or {}), **{x["addr"]: x.get("name") or x.get("short") or x["addr"] for x in fol}}
    mids = post({"type": "allMids"}) or {}
    if IVAN_ADDR in follow and "ivan" not in src_get:
        src_get = {**src_get, "ivan": ivan_reader(env, st)}
    watch = list(dict.fromkeys(follow + [o["whale"] for o in desk.get("open") or []]))
    states, opened, errors = {}, {}, []
    gone = dict(st.get("gone") or {})
    vanished = []
    # 06.11 (owner: copy lag median 341 s): all watched whales are polled at once (one thread each, ≤ POLL_WORKERS) — a pass takes
    # about one slow answer instead of the sum of all; each source still gets ≤ 1 request per whale per pass
    from concurrent.futures import ThreadPoolExecutor
    t_poll = time.time()
    with ThreadPoolExecutor(max_workers=max(1, min(POLL_WORKERS, len(watch)))) as ex:
        futs = {a: ex.submit(poll, a, mids, post, okx_get, src_get) for a in watch}
    results = {}
    for a, f in futs.items():
        try:
            results[a] = (f.result(), None)
        except Exception as e:  # noqa: BLE001
            results[a] = (None, e)
    poll_s = round(time.time() - t_poll, 2)
    for a in watch:
        res_, err = results[a]
        try:
            if err is not None:
                raise err
            states[a], op = res_
            opened.update({(a,) + k: v for k, v in op.items()})
            gone.pop(a, None)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{names.get(a, a)[:16]}: {str(e)[:80]}")
            # 05.11 bug fix (Luckyboytt): a lead trader who left OKX copy trading answers «Trader doesn't exist» forever — his
            # copies were held blind until their −8 % stop. Gone for GONE_MS and no longer followed → his copies are closed.
            if any(m in str(e) for m in GONE_MARKS):
                gone.setdefault(a, now)
                if a not in follow and now - int(gone[a]) >= GONE_MS:
                    states[a] = []
                    vanished.append(a)
            else:
                gone.pop(a, None)
    gone = {a: t for a, t in gone.items() if a in watch}
    # 06.11 (owner): the fixed list (whales_top.json «close_unfollowed»); 09.11: a leaver's copies run to his exit — only blind ones
    # (hidden positions / not seen for 1 h) are closed; only on a fresh feed. Leavers stay polled: watch = follow + open copies
    desk = W.desk_step(desk, follow, states, mids, now, ov.active(ov.load(), now), quality,
                       close_unfollowed=bool(fresh and wtop.get("close_unfollowed")),
                       close_whales=set(wtop.get("close_now") or {}) if fresh else None)   # 10.11: fast exits from column A
    why = "кит исчез с OKX (Trader doesn't exist ≥ 30 мин)"
    for x in (desk.get("events") or []) + (desk.get("closed") or []):
        if x.get("whale") in vanished and x.get("why") == "кит закрыл" and (x.get("t") == now or x.get("closed_t") == now):
            x["why"] = why
    # copy latency: how long after the whale opened we opened (sources that publish the open time: OKX, Binance)
    lags = list((st.get("lags") or [])[-49:])
    by_src = {k: list(v)[-29:] for k, v in (st.get("lags_src") or {}).items() if isinstance(v, list)}
    for e in desk.get("events") or []:
        if e.get("t") == now and e.get("kind") == "open":          # a join copies an older position — not a latency
            t0 = opened.get((e["whale"], e["coin"], e["side"]))
            if t0 and 0 <= now - t0 <= 6 * 3_600_000:
                lags.append(round((now - t0) / 1000))
                src = str(e["whale"]).split(":")[0] if ":" in str(e["whale"]) else "hl"
                by_src.setdefault(src, []).append(lags[-1])
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None  # noqa: E731
    desk["live"] = {"t": now, "whales": len(watch), "polled": len(states), "errors": errors[:5], "follow_asof": wtop.get("asof"), "poll_s": poll_s,
                    "lag_s_med": med(lags), "lag_s_last": lags[-1] if lags else None, "n_lags": len(lags),
                    # 05.11 (backlog 88a): which source is slow — {source: {med, n}}
                    "lag_by_src": {k: {"med": med(v), "n": len(v)} for k, v in sorted(by_src.items()) if v}}
    st.update(desk=desk, names=names, lags=lags, lags_src=by_src, t=now, gone=gone)
    pub = W.desk_public(desk, names)
    _publish(st, desk, now, env, put)
    if persist:
        save(st, env)
    paper = pub["paper"]
    fset = set(follow)
    # 06.11 (owner: after the 00:00 day-stop lift the account opened two copies of a whale who had left the list): every copy says whether
    # its whale is followed now — okx_futures never opens a new entry for follow=False (copies already held are still managed)
    paper["open"] = [{**t, "follow": t.get("whale") in fset} for t in paper.get("open") or []]
    return {**paper, "live": desk["live"], "util_pct": desk.get("util_pct")}


def _sig(desk: dict) -> str:
    return json.dumps([[o.get("whale"), o.get("coin"), o.get("side"), o.get("opened_t"), o.get("stop")] for o in desk.get("open") or []]
                      + [len(desk.get("closed") or [])])


def _publish(st: dict, desk: dict, now: int, env=None, put=None) -> bool:
    """web/whales_live.json on main (raw desk without the «seen» baseline) — when the copies changed (≥ PUB_MS apart) or every PUB_IDLE_MS"""
    sig, last = _sig(desk), int(st.get("pub_t") or 0)
    if not ((sig != st.get("pub_sig") and now - last >= PUB_MS) or now - last >= PUB_IDLE_MS):
        return False
    doc = {"asof": now, "desk": {k: v for k, v in desk.items() if k not in ("seen", "seen_t", "join_wait")}, "names": st.get("names") or {},
           "about_ru": "Стол «Киты» на сервере: позиции выбранных китов проверяются каждую минуту (cnq/execution/whale_live.py)."}
    doc["desk"]["history"] = (desk.get("history") or [])[-500:]
    ok = False
    try:
        if put is not None:
            ok = bool(put(doc))
        else:
            e = os.environ if env is None else env
            tok = e.get("CNQ_GH_TOKEN")
            if tok:
                from . import ivan_watch
                hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-whale-live"}
                body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
                ok = ivan_watch.put_contents(f"https://api.github.com/repos/{e.get('CNQ_GH_REPO') or REPO}/contents/{PATH}", hdr,
                                             base64.b64encode(body.encode()).decode(), "whales live: update")
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        st["pub_t"], st["pub_sig"] = now, sig
    return ok

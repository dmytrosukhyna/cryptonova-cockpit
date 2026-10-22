"""«Автопилот OKX · активный спот» (22.10.2026) — the active-spot sleeve (OKX_ACT_SHARE, 0.10 of the account) follows the «Импульс
BTC» spot desk (web/paper.json → profiles.imps: when BTC jumps ≥ 2.5 % in an hour it buys the most liquid alts on spot, up to 5
positions × 20 % of its account, exits after 12 h or at its wide stop). While that desk has no open trade the sleeve waits in USDT
(«ждёт сигнала»).

  • a NEW desk trade (≤ 45 min old, after this part started) → a spot buy of the same coin (limit IOC, at most OKX_SLIP above the
    best price) for the same share of the sleeve as the desk's position is of its account;
  • the desk closed it (time exit, stop, target) or cut it (qty_left) → the bought coins are sold (limit IOC, at most OKX_SLIP below
    the best price); a part left unsold is tried again a minute later.
Spot only: no leverage, no borrowing, nothing moved between accounts. The coins bought here are remembered in
~/.cryptonova_okx_active.json and the spot rebalance (okx_autopilot) never counts or sells them. Kill switch, live / demo and the
key checks are the spot autopilot's. Off with OKX_ACTIVE=off. Not financial advice. Stdlib only."""
from __future__ import annotations

import json
import os
import time
import urllib.request

from . import myspot_watch as ms
from . import okx_alloc as al
from . import okx_autopilot as ap

LOG = "data/okx_active.jsonl"
FRESH_MS = 45 * 60_000
MAX_POS = 5
SRC = "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/paper.json"


def _env(env=None) -> dict:
    return os.environ if env is None else env


def enabled(env=None) -> bool:
    return (_env(env).get("OKX_ACTIVE") or "on").strip().lower() not in ("off", "no", "0", "false")


def state_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_ACT_STATE") or "~/.cryptonova_okx_active.json")


def load_state(env=None) -> dict:
    try:
        with open(state_path(env), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict, env=None) -> None:
    p = state_path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    try:
        os.chmod(p + ".tmp", 0o600)
    except OSError:
        pass
    os.replace(p + ".tmp", p)


def held(env=None) -> dict:
    """{COIN: quantity} bought by this sleeve and not sold yet — the spot rebalance leaves these alone."""
    out = {}
    for m in (load_state(env).get("trades") or {}).values():
        out[m["ccy"]] = out.get(m["ccy"], 0.0) + float(m.get("qty") or 0)
    return out


def fetch_desk(env=None) -> dict:
    src = _env(env).get("OKX_ACT_SRC") or _env(env).get("CNQ_MIRROR_SRC") or SRC
    req = urllib.request.Request(src + f"?t={int(time.time())}", headers={"Cache-Control": "no-cache", "User-Agent": "cnq-okx-active"})
    with urllib.request.urlopen(req, timeout=20) as r:
        p = json.loads(r.read().decode())
    return (p.get("profiles") or {}).get("imps") or {}


def _sell(cl: ap.Okx, ccy: str, quote: str, qty: float, slip: float, cl_id: str, sleep) -> dict:
    inst_id = f"{ccy}-{quote}"
    inst, tk = cl.instrument(inst_id), cl.ticker(inst_id)
    bid = ms._f((tk or {}).get("bidPx")) or ms._f((tk or {}).get("last"))
    if not inst or not bid:
        return {"filled": 0.0, "why": f"нет пары {inst_id}"}
    q = ap.floor_to(min(qty, cl.avail(ccy)), float(inst["lotSz"]))
    if q < float(inst["minSz"]):
        return {"filled": qty, "why": "остаток меньше минимума OKX — считаю проданным", "dust": True}
    o = {"inst": inst_id, "side": "sell", "sz": ap.fmt(q, inst["lotSz"]), "px": ap.fmt(ap.floor_to(bid * (1 - slip), float(inst["tickSz"])), inst["tickSz"])}
    return ap._fill(cl, o, cl_id, sleep)


def step(env=None, opener=None, desk: dict | None = None, now: int | None = None, sleep=time.sleep, cl: ap.Okx | None = None,
         total: float | None = None, log_path: str | None = None, save: bool = True) -> dict:
    now = int(now or time.time() * 1000)
    lp = log_path or LOG
    res = {"bought": 0, "sold": 0, "errors": 0, "skipped": [], "why": None}
    if not enabled(env):
        res["why"] = "активный спот выключен (OKX_ACTIVE=off)"
        return res
    if ap.stopped(env):
        res["why"] = "включён стоп"
        return res
    st = load_state(env)
    st.setdefault("started", now)
    st.setdefault("trades", {})
    st.setdefault("skip", {})
    d = desk if desk is not None else fetch_desk(env)
    want = {str(t["id"]): t for t in d.get("open") or [] if t.get("id") and t.get("symbol") and t.get("side") == "LONG"}
    if not want and not st["trades"]:
        if save:
            save_state(st, env)
        return res
    cl = cl or ap.connect(env, opener, ap.load_state(env), sleep)
    perm = ap.perm_of(cl.config())
    bad = ("ключ с правом ВЫВОДА" if "withdraw" in perm else "у ключа нет права «Торговля»" if "trade" not in perm else ap.world_ok(cl, env))
    if bad:
        res["why"] = bad
        return res
    quote = ap.pick_quote(cl, env) or "USDT"
    slip = ap.limits(env)["slip"]
    seq = 0
    # exits and cuts
    for tid, m in list(st["trades"].items()):
        t = want.get(tid)
        keep_frac = 0.0 if t is None else ((float(t["qty_left"]) / float(t["qty"])) if t.get("qty_left") is not None and float(t.get("qty") or 0) > 0 else 1.0)
        sell_q = float(m["qty"]) - float(m["q0"]) * keep_frac
        if sell_q <= float(m["q0"]) * 0.001:
            continue
        seq += 1
        try:
            f = _sell(cl, m["ccy"], quote, sell_q, slip, f"cnqas{now // 1000}{seq:03d}", sleep)
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            ap.log({"ev": "sell_error", "id": tid, "ccy": m["ccy"], "error": str(e)[:200]}, lp)
            continue
        m["qty"] = max(0.0, float(m["qty"]) - float(f.get("filled") or 0))
        ap.log({"ev": "sell", "id": tid, "ccy": m["ccy"], "want": sell_q, **{k: v for k, v in f.items() if k != "dust"}}, lp)
        if f.get("filled"):
            res["sold"] += 1
        if m["qty"] <= float(m["q0"]) * 0.001 or (t is None and f.get("dust")):
            st["trades"].pop(tid)
    # entries
    st["skip"] = {k: v for k, v in st["skip"].items() if k in want}
    fresh = [t for tid, t in want.items() if tid not in st["trades"] and tid not in st["skip"]
             and now - int(t.get("opened") or 0) <= FRESH_MS and int(t.get("opened") or 0) >= int(st["started"]) - FRESH_MS]
    if fresh:
        if total is None:
            total = sum(r["usd"] for r in ms.assets(ms.read_raw(cl.fetch, cl.host)))
        bud = total * al.shares(env)["active"]
        deq = float(d.get("equity") or d.get("start_equity") or 10_000)
        for t in fresh:
            tid = str(t["id"])
            if len(st["trades"]) >= MAX_POS:
                res["skipped"].append({"id": tid, "why": "уже 5 позиций активного спота"})
                continue
            base, mult = al.okx_base(str(t["symbol"]))
            inst_id = f"{base}-{quote}"
            inst, tk = cl.instrument(inst_id), cl.ticker(inst_id)
            ask = ms._f((tk or {}).get("askPx")) or ms._f((tk or {}).get("last"))
            if not inst or not ask:
                st["skip"][tid] = f"на OKX нет спот-пары {inst_id}"
                res["skipped"].append({"id": tid, "why": st["skip"][tid]})
                continue
            if abs(ask * mult / float(t["entry"]) - 1) > 0.15:
                st["skip"][tid] = f"цена {inst_id} отличается от входа сигнала больше чем на 15%"
                res["skipped"].append({"id": tid, "why": st["skip"][tid]})
                continue
            usd = float(t.get("qty_left") or t["qty"]) * float(t["entry"]) / deq * bud
            usd = min(usd, cl.avail(quote) * 0.98)
            q = ap.floor_to(usd / ask, float(inst["lotSz"]))
            if q < float(inst["minSz"]) or q * ask < ap.MIN_ORDER:
                st["skip"][tid] = f"меньше минимального ордера (≈ {ap.usd(q * ask)})"
                res["skipped"].append({"id": tid, "why": st["skip"][tid]})
                continue
            seq += 1
            o = {"inst": inst_id, "side": "buy", "sz": ap.fmt(q, inst["lotSz"]), "px": ap.fmt(ap.ceil_to(ask * (1 + slip), float(inst["tickSz"])), inst["tickSz"])}
            try:
                f = ap._fill(cl, o, f"cnqab{now // 1000}{seq:03d}", sleep)
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                ap.log({"ev": "buy_error", "id": tid, "inst": inst_id, "error": str(e)[:200]}, lp)
                continue
            ap.log({"ev": "buy", "id": tid, "inst": inst_id, **f}, lp)
            if f["filled"] > 0:
                st["trades"][tid] = {"ccy": base, "q0": f["filled"], "qty": f["filled"], "opened": now, "px": f["avg_px"]}
                res["bought"] += 1
    if save:
        save_state(st, env)
    return res


def plan_lines(env=None, desk: dict | None = None, now: int | None = None, total: float = 0.0) -> list[str]:
    now = int(now or time.time() * 1000)
    bud = total * al.shares(env)["active"]
    L = [f"АКТИВНЫЙ СПОТ · Импульс BTC — {al._p(al.shares(env)['active'])} счёта ≈ {ap.usd(bud)}"
         + ("" if enabled(env) else " — ВЫКЛЮЧЕН (OKX_ACTIVE=off), деньги в USDT")]
    try:
        d = desk if desk is not None else fetch_desk(env)
    except Exception as e:  # noqa: BLE001
        return L + [f"  сигналы не загрузились: {str(e)[:100]}"]
    st = load_state(env)
    mine = st.get("trades") or {}
    opened = [t for t in d.get("open") or [] if t.get("side") == "LONG"]
    if not opened and not mine:
        return L + ["  ждёт сигнала: у стола «Импульс BTC» нет открытых сделок — эта часть лежит в USDT."]
    deq = float(d.get("equity") or 10_000)
    started = int(st.get("started") or now)
    for t in opened:
        tid = str(t["id"])
        if tid in mine:
            L.append(f"  {t['symbol']}: уже куплено {mine[tid]['qty']:g}")
        elif now - int(t.get("opened") or 0) > FRESH_MS or int(t.get("opened") or 0) < started - FRESH_MS:
            L.append(f"  {t['symbol']}: сигнал старше 45 минут — не догоняем")
        else:
            usd = float(t.get("qty_left") or t["qty"]) * float(t["entry"]) / deq * bud
            L.append(f"  {t['symbol']}: КУПИТЬ на ≈ {ap.usd(usd)} (лимит IOC) — сигнал «Импульс BTC», выход вместе со столом")
    for tid, m in mine.items():
        if tid not in {str(t["id"]) for t in opened}:
            L.append(f"  {m['ccy']}: ПРОДАТЬ {m['qty']:g} — стол закрыл сделку")
    return L


def status(env=None) -> dict:
    st = load_state(env)
    return {"enabled": enabled(env), "open": [{"ccy": m["ccy"], "qty": m["qty"]} for m in (st.get("trades") or {}).values()]}

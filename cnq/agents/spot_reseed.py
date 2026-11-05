"""One-time rebuild of the spot models after the 04.11 19:28 UTC state loss (owner 05.11 09:07, option 1: «пересобрать спот-модель с
нуля от того, что реально лежит на счёте OKX»).

Why: the GitHub pipeline lost its Actions cache and restored a 15-day-old data/agents.sqlite; the state guard (scripts/ci_state_guard.py)
then skipped the spot department so the REAL account (okx_autopilot mirrors «CRYPTO NOVA портфель») could not follow a rolled-back
model. The lost state cannot be restored, so the models are rebuilt once, migration id from config/spot_reseed.json:

* «ideas» (CRYPTO NOVA портфель) — from the live OKX account as the server publishes it (web/public_real.json, ≤ MAX_AGE_MS old):
  each coin's share of the model = (its share of the account − the extra core share okx_alloc.core_extra) ÷ the spot share, so the
  autopilot's targets (okx_alloc.targets + core_extra) equal what the account holds → its first pass places no orders (checked by
  plan_gaps before anything is written, and again on the server: okx_autopilot.run_once never trades the first pass of a model that
  carries a new «reseed» id). The rest is cash. Entry, entry time, high and kind of a coin come from the last published model (its real
  history); a coin it did not hold gets today's price. The model's value continues the published index (no jump on the curve), and
  the published «history_all» points are written back into spot_ideas_value.
* «cons», «high», «bal», «mod» (display only, no real money) — positions and value history as last published in web/portfolio.json; cash
  (or «realized» for «mod») is what makes the published index; meta the published one has (started, last_weekly, …) is kept, the rest
  starts fresh. «meme» lives in web/memes.json, nothing to seed.

The pipeline run that applies it publishes the rebuilt «ideas» into web/portfolio.json itself and skips the spot step once (the first
autopilot pass must see the rebuilt model); from the next run the normal rules apply. Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time

from ..execution import okx_alloc as al
from . import spot_profiles as SP

MAX_AGE_MS = 45 * 60_000
CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "spot_reseed.json")
DISPLAY = ("cons", "high", "bal", "mod")


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def account_shares(real: dict, w: dict) -> tuple[dict, float, dict]:
    """web/public_real.json → ({coin: share of the model}, model cash share, {coin: share of the account}). Coins below $1 are ignored."""
    al_ = real.get("alloc") or {}
    total = _f(real.get("equity_usd"), 0.0) - _f(al_.get("active_coins_usd"), 0.0)
    if total <= 0:
        raise ValueError("в public_real.json нет стоимости счёта")
    acct = {str(x["sym"]).upper(): _f(x.get("usd"), 0.0) / total for x in real.get("spot") or [] if _f(x.get("usd"), 0.0) >= 1.0}
    ce = al.core_extra(w)
    model = {}
    for c, s in acct.items():
        m = (s - ce.get(c, 0.0)) / w["spot"]
        if m > 1e-6:
            model[c] = m
    tot = sum(model.values())
    if tot > 1:
        raise ValueError(f"монеты счёта больше спот-доли ({tot:.2%} модели) — проверь доли OKX_SPOT_SHARE / OKX_CORE_SHARE")
    return model, 1 - tot, acct


def plan_gaps(model: dict, acct: dict, w: dict, total: float, band_frac: float = 0.003, min_usd: float = 10.0) -> list[dict]:
    """the orders okx_autopilot.build would place for these shares (its own rule: |gap| ≥ max($10, 0.3 % of the account), a coin out
    of the model is sold) — [] means model == account"""
    tgt = {c: w["spot"] * s for c, s in model.items()}
    for c, f in al.core_extra(w).items():
        tgt[c] = tgt.get(c, 0.0) + f
    band = max(min_usd, band_frac * total)
    out = []
    for c in sorted(set(tgt) | set(acct)):
        gap = (tgt.get(c, 0.0) - acct.get(c, 0.0)) * total
        now = acct.get(c, 0.0) * total
        if (gap < 0 and (tgt.get(c, 0.0) == 0 and now >= min_usd or -gap >= band)) or (gap > 0 and gap >= (min_usd if now < min_usd else band)):
            out.append({"coin": c, "usd": round(gap, 2)})
    return out


def _hist_points(p: dict) -> list:
    pts = {int(h["time"]): float(h["v"]) for h in (p.get("history_all") or []) + (p.get("history") or [])
           if isinstance(h, dict) and h.get("time") and _f(h.get("v")) is not None}
    return sorted(pts.items())


def _wipe(conn, T: dict) -> None:
    for k in ("pos", "val", "meta"):
        conn.execute(f"DELETE FROM {T[k]}")


def seed_ideas(conn, pub: dict, real: dict, w: dict, px: dict, now: int, mig: str) -> dict:
    """rebuild spot_ideas_* from the account; → the published «ideas» entry for web/portfolio.json (positions as shares)"""
    model, cash, acct = account_shares(real, w)
    p0 = (pub.get("portfolios") or {}).get("ideas") or {}
    old = {str(x.get("symbol")).upper(): x for x in p0.get("positions") or []}
    pts = _hist_points(p0)
    idx = pts[-1][1] if pts else _f(p0.get("index"), 100.0)
    V = idx / 100.0
    rp = SP.RiskPortfolio(conn, SP.IDEAS["key"], {**SP.IDEAS, "about": SP.IDEAS_ABOUT})
    T = {"pos": rp.T_POS, "val": rp.T_VAL, "meta": rp.T_META}
    _wipe(conn, T)
    conn.executemany(f"INSERT OR REPLACE INTO {rp.T_VAL} VALUES(?,?)", pts + [(now, round(idx, 2))])
    rows = []
    for c, s in sorted(model.items(), key=lambda x: -x[1]):
        o = old.get(c) or {}
        price = _f(px.get(c)) or _f(o.get("price"))
        if not price:
            raise ValueError(f"нет цены {c} — пересборка невозможна")
        entry = _f(o.get("entry")) or price
        kind = o.get("bucket") or ("core" if c in al.CORE else "mid")
        role = o.get("role") or "buy"
        weight = V * s * entry / price                     # cost units: market value now = V × share
        conn.execute(f"INSERT OR REPLACE INTO {rp.T_POS} VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (c, f"{kind}:{role}", weight, entry, int(_f(o.get("entry_time"), now)), max(_f(o.get("high"), price), price), "OPEN",
                      "пересобрано 05.11 по реальному счёту OKX", int(_f(o.get("fixes"), 0)), None))
        rp._set_meta(f"px:{c}", price)
        h = SP.horizon_of(kind, role)
        P = SP.IDEAS
        rows.append({"symbol": c, "bucket": kind, "role": role, "kind_ru": SP.KIND_RU.get(kind, kind), "role_ru": SP.IDEAS_ROLE_RU.get(role, role),
                     "name": o.get("name") or c, "weight": round(s, 4), "cost_weight": round(weight, 6), "entry": entry, "price": price,
                     "entry_time": int(_f(o.get("entry_time"), now)), "status": "OPEN", "fixes": int(_f(o.get("fixes"), 0)),
                     "target_weight": o.get("target_weight") or (SP.core_target(c) if kind == "core" else P["buy_w"]),
                     "levels": {"stop": round(entry * (1 + (P["stop_small"] if h == "short" else P["stop_buy"])), 8) if h != "long" else None,
                                "take_half": round(entry * P["body_x"], 8) if h == "short" and not o.get("fixes") else None},
                     "value_usd": round(10_000 * V * s, 2), "pnl_pct": round((price / entry - 1) * 100, 2)})
    rp._set_cash(V * cash)
    started = _f(p0.get("started"))
    for k, v in (("started", started or now), ("last_weekly", _f(p0.get("last_weekly"))), ("okx_swap_v1", now), ("okx_swap", 0.0),
                 ("idle_since", 0.0), ("topup_bull", 1.0 if pub.get("btc_bull") else 0.0)):
        if v is not None:
            rp._set_meta(k, v)
    why = (f"Модель пересобрана по реальному счёту OKX ({mig}): база потеряна 04.11, доли монет — как на счёте, кэш {cash:.1%}. "
           "Первый проход автопилота — без ордеров.")
    hist = [{"time": t, "v": v} for t, v in pts + [(now, round(idx, 2))]]
    return {**p0, "positions": rows, "cash_pct": round(cash * 100), "invested_pct": round((1 - cash) * 100), "index": round(idx, 2),
            "equity_usd": round(10_000 * V, 2), "history": hist[-60:], "history_all": hist, "actions": [],
            "recent_actions": list(p0.get("recent_actions") or [])[:20],
            "reseed": {"id": mig, "note_ru": why, "t": now, "account_asof": real.get("asof"), "model_cash": round(cash, 4), "account": {c: round(s, 4) for c, s in acct.items()}}}


def seed_display(conn, pub: dict, key: str, now: int) -> int:
    """positions + value history of a display portfolio as published; cash (mod: realized) so that its value = the published index"""
    from . import portfolio as PF
    p = (pub.get("portfolios") or {}).get(key) or {}
    pts = _hist_points(p)
    idx = pts[-1][1] if pts else _f(p.get("index"))
    if idx is None:
        return 0
    V = idx / 100.0
    if key == "bal":
        rp = PF.BalancedPortfolio(conn).rp
        T = {"pos": rp.T_POS, "act": rp.T_ACT, "val": rp.T_VAL, "meta": rp.T_META}
    else:
        T = PF._spot_tables(conn, key)
    _wipe(conn, T)
    conn.executemany(f"INSERT OR REPLACE INTO {T['val']} VALUES(?,?)", pts or [(now, idx)])
    mv = cost = 0.0
    n = 0
    for x in p.get("positions") or []:
        s, e = str(x.get("symbol") or "").upper(), _f(x.get("entry"))
        wt = _f(x.get("cost_weight")) if key == "bal" else _f(x.get("weight"))
        if not s or not e or not wt:
            continue
        price = _f(x.get("price")) or e
        mv += wt * price / e
        cost += wt
        n += 1
        if key == "mod":
            conn.execute("INSERT OR REPLACE INTO spot_portfolio VALUES(?,?,?,?,?,?,?,?)",
                         (s, x.get("bucket"), wt, e, int(_f(x.get("entry_time"), now)), _f(x.get("high"), e), "OPEN", x.get("reason") or ""))
        else:
            b = x.get("bucket") if key != "bal" else f"{x.get('bucket')}:{x.get('role')}" if x.get("role") and x.get("role") != x.get("bucket") else x.get("bucket")
            conn.execute(f"INSERT OR REPLACE INTO {T['pos']} VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (s, b, wt, e, int(_f(x.get("entry_time"), now)), _f(x.get("high"), e), "OPEN", x.get("reason") or "", int(_f(x.get("fixes"), 0)),
                          _f(x.get("last_fix"))))
        conn.execute(f"INSERT OR REPLACE INTO {T['meta']} VALUES(?,?)", (f"px:{s}", price))
    if key == "mod":
        conn.execute("INSERT OR REPLACE INTO spot_mod_meta VALUES('realized',?)", (V - 1 - (mv - cost),))
    else:
        conn.execute(f"INSERT OR REPLACE INTO {T['meta']} VALUES('cash',?)", (max(0.0, V - mv),))
    for k in ("started", "last_weekly", "last_rebalance"):
        if _f(p.get(k)) is not None:
            conn.execute(f"INSERT OR REPLACE INTO {T['meta']} VALUES(?,?)", (k, _f(p.get(k))))
    if key == "bal" and p.get("mode"):
        conn.execute(f"INSERT OR REPLACE INTO {T['meta']} VALUES('mode',?)", (1.0 if p.get("mode") == "bull" else 0.0,))
    return n


def apply(conn: sqlite3.Connection, pub: dict, real: dict, px: dict, now: int | None = None, cfg: dict | None = None,
          w: dict | None = None) -> dict | None:
    """the whole migration, once per id → {"id", "ideas": published entry, "gaps": [], "display": {key: n}}; None when already applied /
    not requested. Raises (nothing written) when the account snapshot is missing or stale, or the plan check finds an order."""
    now = int(now or time.time() * 1000)
    if cfg is None:
        try:
            with open(CFG, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:  # noqa: BLE001
            return None
    mig = str((cfg or {}).get("id") or "")
    if not mig:
        return None
    conn.execute("CREATE TABLE IF NOT EXISTS migrations(id TEXT PRIMARY KEY, time INTEGER, note TEXT)")
    if conn.execute("SELECT 1 FROM migrations WHERE id=?", (mig,)).fetchone():
        return None
    if not real or real.get("stale") or now - int(_f(real.get("asof"), 0)) > MAX_AGE_MS:
        raise ValueError("снимок реального счёта (web/public_real.json) старый или помечен stale — пересборку не делаю")
    al_ = real.get("alloc") or {}
    w = w or {"spot": _f(al_.get("spot_pct"), 65.0) / 100, "core": _f(al_.get("core_pct"), 10.0) / 100}
    model, cash, acct = account_shares(real, w)
    total = _f(real.get("equity_usd"), 0.0) - _f(al_.get("active_coins_usd"), 0.0)
    gaps = plan_gaps(model, acct, w, total)
    if gaps:
        raise ValueError(f"проверка плана: после пересборки автопилот поставил бы ордера {gaps} — не пишу")
    ideas = seed_ideas(conn, pub, real, w, px, now, mig)
    disp = {k: seed_display(conn, pub, k, now) for k in DISPLAY}
    conn.execute("INSERT OR REPLACE INTO migrations VALUES(?,?,?)", (mig, now, json.dumps({"model": model, "cash": cash, "display": disp})))
    conn.commit()
    return {"id": mig, "ideas": ideas, "gaps": gaps, "display": disp, "model": model, "cash": cash, "account": acct, "total": total}

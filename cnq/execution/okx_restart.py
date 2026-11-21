"""Restart of the PUBLIC track record of the real OKX account (owner 21.11.2026 07:34–07:36, explicit).

The owner brings the account to about $10 000 himself (a BTC deposit — we never move funds). When the coordinator says the deposit
arrived, config/owner_overrides.json «restart_21_11» gets "armed": true. From then on:
  • the public start = the account's equity at the first armed public_real pass whose equity is ≥ "min_usd" (the deposit landed),
    "since" = that moment. Everything public counts from it: public_real curve / fut_curve / today / pnl / closed / actions / flows,
    and every reader of web/public_real.json (app_home public_portfolio, the site's and app's «заработано всего»);
  • the deposit is NOT a result: it lands before "since", so it is inside the start, not in the flows (flows count from "since");
  • futures positions open at the restart: their P/L up to the restart (upl0) is taken off the public futures result;
  • the old history (since 22.10.2026) leaves every public output and is kept owner-only in ARCHIVE (a server-side file, mode 600):
    the last public doc, the state's curves and flows, the old start, the closed swaps since 22.10;
  • the public label LABEL_RU (public_real.json «label_ru»); nothing public claims a result before 21.11.2026;
  • the CRYPTO NOVA AI portfolio model opens its meme sleeve (≤ 5 % — spot_profiles MEME) — only while armed.
"armed": false (or no «restart_21_11») = nothing changes. Disarming after an activation brings the old start back for the numbers,
but the old curve points are then only in the archive. Stdlib only."""
from __future__ import annotations

import json
import os
import time

KEY = "restart_21_11"
LABEL_RU = "Реальный счёт CRYPTO NOVA AI · с 21.11.2026"
MIN_USD = 8000.0                       # the activation waits until the deposit shows in the equity (owner: «около $10 000»)


def _env(env=None) -> dict:
    return os.environ if env is None else env


def cfg(env=None, ov: dict | None = None) -> dict:
    if ov is None:
        from . import owner_overrides
        ov = owner_overrides.load(env=env)
    c = (ov or {}).get(KEY)
    return c if isinstance(c, dict) else {}


def armed(env=None, ov: dict | None = None) -> bool:
    return cfg(env, ov).get("armed") is True


def _home(env=None) -> str:
    p = _env(env).get("OKX_PUBLIC_STATE") or "~/.cryptonova_public_real.json"
    return os.path.dirname(os.path.expanduser(p)) or "."


def state_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_RESTART_STATE") or os.path.join(_home(env), ".cryptonova_restart_21_11.json"))


def archive_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_RESTART_ARCHIVE") or os.path.join(_home(env), ".cryptonova_public_real_archive_2210.json"))


def _write(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load(env=None) -> dict:
    try:
        with open(state_path(env), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("t") and d.get("usd") else {}
    except Exception:  # noqa: BLE001
        return {}


def public_start(env=None, ov: dict | None = None) -> dict | None:
    """{"usd", "t", "src": "restart"} while armed and activated, else None (myspot_sub.start falls back to 22.10)"""
    if not armed(env, ov):
        return None
    r = load(env)
    if not r or (cfg(env, ov).get("id") and r.get("id") != cfg(env, ov).get("id")):
        return None
    return {"usd": round(float(r["usd"]), 2), "t": int(r["t"]), "src": "restart",
            "label_ru": str(cfg(env, ov).get("label_ru") or LABEL_RU), "upl0": float(r.get("upl0") or 0.0)}


def label(env=None, ov: dict | None = None) -> str | None:
    return (public_start(env, ov) or {}).get("label_ru")


def maybe_activate(s: dict, pr_state: dict, now: int, env=None, closed_old=None, ov: dict | None = None) -> dict | None:
    """called by public_real.run after an ok read (s = myspot_sub.read). Armed, not yet activated and the equity ≥ min_usd →
    archive the old public history (owner-only), drop it from the public state, write the new start. Returns the restart record
    when it activated now, else None. closed_old() → the closed swaps since the old start (for the archive; may fail)."""
    if not armed(env, ov) or public_start(env, ov):
        return None
    c = cfg(env, ov)
    eq = float(s.get("total_usd") or 0.0)
    if eq < float(c.get("min_usd") or MIN_USD):
        return None
    old = dict(s.get("start") or {})
    try:
        closed = closed_old() if closed_old else None
    except Exception:  # noqa: BLE001 — the archive never blocks the restart
        closed = None
    _write(archive_path(env), {"archived_t": now, "why_ru": "Старт публичной истории перенесён на 21.11.2026 (решение владельца 21.11). "
                                                            "История с 22.10.2026 — только для владельца и исследований.",
                               "old_start": old, "last_public": pr_state.get("last"), "curve": pr_state.get("curve"),
                               "fut_curve": pr_state.get("fut_curve"), "flows": pr_state.get("flows"),
                               "flows_checked": pr_state.get("flows_checked"), "closed": closed})
    upl = sum(float(p.get("upl") or 0.0) for p in (s.get("futures") or {}).get("positions") or [])
    rec = {"id": c.get("id") or KEY, "t": int(now), "usd": round(eq, 2), "upl0": round(upl, 2), "old_since": old.get("t"),
           "old_start_usd": old.get("usd"), "activated_t": int(time.time() * 1000)}
    _write(state_path(env), rec)
    for k in ("curve", "fut_curve", "last"):        # build() starts the public curves at [since, 0]
        pr_state.pop(k, None)
    return rec


# ── 21.11 07:46 (owner): the coins he transferred with the deposit ─────────────────────────────────────────────────────────
# cfg "deposit": {"keep": {SYM: stop px}, "sell": {SYM: $ per pass}, "hold": [SYM]} — keep: never traded by the rebalancer, an
# exchange stop at the given price (okx_spot_stops); sell: sold by the rebalancer in parts ≤ $ per 5-min pass (IOC limit ≤ 0.4 %
# under the bid, never a market order); hold: not touched at all until evaluated. Works whether armed or not.
def _dep(env=None, ov: dict | None = None) -> dict:
    d = cfg(env, ov).get("deposit")
    return d if isinstance(d, dict) else {}


def _sym(x) -> str:
    s = str(x or "").strip().upper()
    return s if s.isascii() and s.isalnum() and len(s) <= 20 else ""


def hold_coins(env=None, ov: dict | None = None) -> set:
    d = _dep(env, ov)
    return {_sym(c) for c in list(d.get("hold") or []) + list((d.get("keep") or {}).keys()) if _sym(c)}


def keep_stops(env=None, ov: dict | None = None) -> dict:
    out = {}
    for c, px in ((_dep(env, ov).get("keep") or {}).items()):
        try:
            if _sym(c) and float(px) > 0:
                out[_sym(c)] = float(px)
        except (TypeError, ValueError):
            continue
    return out


def sell_slow(env=None, ov: dict | None = None) -> dict:
    out = {}
    for c, v in ((_dep(env, ov).get("sell") or {}).items()):
        try:
            if _sym(c) and float(v) > 0:
                out[_sym(c)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def usdt_min(env=None, ov: dict | None = None) -> float:
    """21.11 07:46: the USDT buffer of the spot rebalance (share of the whole account) while BTC is not bull; 0 = none"""
    try:
        v = float(cfg(env, ov).get("usdt_min_pct") or 0.0)
        return v if 0 < v < 0.5 else 0.0
    except (TypeError, ValueError):
        return 0.0

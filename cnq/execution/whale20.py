"""«Киты-20» — DEMO copy desk next to the real «Киты» desk (13.11.2026, owner, backlog 160: «учитываем и используем»).

Who: whales_top.json «k20» (cnq/agents/whale_study.k20_select) — up to 20 traders ranked by the 60-day delayed-copy test (RA),
the list fixed for 3 days. How: exactly the real desk's rules — cnq/agents/whale_study.desk_step (new positions and joins inside
−3 %…+1 % of the trader's entry, stop −16 % / the trader's, owner overrides, sizing by free margin, ≤ 50 % per trader, 5× cap).
Where: the engine runs it in the background every ENGINE_EVERY cycles (≈ 2 minutes; the real desk polls every ~60 s) and syncs
web/whales20.json like its other files. Paper only: no exchange keys, no orders; okx_futures never reads this file. Off with
CNQ_WHALES20=off. State: CNQ_WHALES20_STATE (default ~/.cryptonova_whales20.json)."""
from __future__ import annotations

import json
import os
import time

from ..agents import whale_study as W
from . import owner_overrides as ov
from . import whale_live as WL

NAME_RU = "Киты-20 · демо (отбор по тесту копии)"
FOLLOW_STALE_MS = 12 * 3_600_000
POLL_WORKERS = 4                       # gentler than the real desk's 8: this desk must never crowd the real one's sources
ENGINE_EVERY = 2


def enabled(env=None) -> bool:
    e = os.environ if env is None else env
    return (e.get("CNQ_WHALES20") or "on").strip().lower() not in ("off", "no", "0", "false")


def state_path(env=None) -> str:
    e = os.environ if env is None else env
    return e.get("CNQ_WHALES20_STATE") or os.path.join(os.path.expanduser("~"), ".cryptonova_whales20.json")


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
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)


def step(wtop: dict | None, env=None, now: int | None = None, post=None, okx_get=None, src_get: dict | None = None,
         st: dict | None = None, persist: bool = True) -> dict:
    """one pass → the public document of web/whales20.json"""
    now = int(now or time.time() * 1000)
    post, okx_get, src_get = post or W._post, okx_get or W._okx_get, dict(src_get or {})
    st = st if st is not None else load(env)
    wtop = wtop if isinstance(wtop, dict) else {}
    k20 = wtop.get("k20") if isinstance(wtop.get("k20"), dict) else {}
    desk = st.get("desk") if isinstance(st.get("desk"), dict) else None
    fresh = now - int(W._f(wtop.get("asof"))) <= FOLLOW_STALE_MS
    fol = [x for x in (k20.get("following") or []) if isinstance(x, dict) and x.get("addr") and not str(x["addr"]).startswith("ivan:")]
    fol = fol if fresh else []
    follow = [x["addr"] for x in fol]
    quality = {x["addr"]: {k: x.get(k) for k in ("win", "pf", "per_day", "pnl_60d", "lev_med")} for x in fol}
    names = {**(st.get("names") or {}), **{x["addr"]: x.get("name") or x.get("short") or x["addr"] for x in fol}}
    errors: list[str] = []
    mids = post({"type": "allMids"}) or {}
    watch = list(dict.fromkeys(follow + [o["whale"] for o in (desk or {}).get("open") or []]))
    states = {}
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    if watch:
        with ThreadPoolExecutor(max_workers=max(1, min(POLL_WORKERS, len(watch)))) as ex:
            futs = {a: ex.submit(WL.poll, a, mids, post, okx_get, src_get) for a in watch}
        for a, f in futs.items():
            try:
                states[a] = f.result()[0]
            except Exception as e:  # noqa: BLE001 — a failed poll changes nothing for that trader (desk_step)
                errors.append(f"{str(names.get(a, a))[:16]}: {str(e)[:80]}")
    desk = W.desk_step(desk, follow, states, mids, now, ov.active(ov.load(), now), quality, close_unfollowed=bool(fresh))
    desk["live"] = {"t": now, "whales": len(watch), "polled": len(states), "errors": errors[:5], "follow_asof": wtop.get("asof"),
                    "poll_s": round(time.time() - t0, 2)}
    st.update(desk=desk, names=names, t=now)
    if persist:
        save(st, env)
    pub = W.desk_public(desk, names)
    pub["name_ru"] = NAME_RU
    pub["paper"]["name"] = NAME_RU
    return {"asof": now, "demo": True, "eligible_real": False, "list": {k: v for k, v in k20.items() if k != "following"},
            "following": fol, "desk": pub,
            "about_ru": "Демо-стол «Киты-20» (решение владельца 13.11, п. 160): те же правила копирования, что у стола «Киты», но до 20 трейдеров, "
                        "отобранных по сумме теста копии за 60 дней. Только бумага; реальный счёт его не читает. Через 2 недели — сравнение со "
                        "столом «Киты» (итог, худший день, просадка, число копий). Статистика прошлого, не обещание."}


def run(out_dir: str = "web", env=None) -> dict:
    try:
        with open(os.path.join(out_dir, "whales_top.json"), encoding="utf-8") as f:
            wtop = json.load(f)
    except Exception:  # noqa: BLE001
        wtop = {}
    doc = step(wtop, env)
    p = os.path.join(out_dir, "whales20.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)
    return doc

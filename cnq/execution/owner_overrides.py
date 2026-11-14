"""Owner overrides for the real futures (04.11.2026, owner: «Шорт по HYPE надо закрывать … шортить HYPE сейчас нет смысла»).

config/owner_overrides.json (in the repo; the server gets it with the engine's git pull, the autopilot restarts onto new code):
  {"set": <ms or ISO>, "until": <ms or ISO>, "close": ["HYPE"], "no_short": ["HYPE"], "no_long": [], "note_ru": "..."}
  • close    — a position in that coin opened BEFORE «set» is closed (any side; reduce-only through the sleeve's ordinary desk-close
               path, and the «Киты» paper desk closes its copy). New trades after «set» are not touched by «close».
  • no_short — no short in that coin is held or opened until «until» (a held one is closed the same way);
  • no_long  — the same for longs.
  • day_stop_ok — trade ids («W-ARB-L-…», or «*» = every copy trade) the owner explicitly approved to open while the futures day stop holds (14.11);
               all other entry checks still apply; expires at «day_stop_ok_until» (or «until»).
After «until» the file does nothing. A missing / broken file = no overrides (it can only REMOVE trades, never add one).
Coins are matched by their base: HYPE, HYPE-USDT-SWAP, kPEPE, 1000PEPE, PEPE all compare as the base coin.
Stdlib only."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "owner_overrides.json")


def base(sym) -> str:
    s = str(sym or "").strip()
    if s[:1] == "k" and s[1:].isupper():
        s = s[1:]
    s = s.upper().split("-")[0]
    return s[4:] if s.startswith("1000") and len(s) > 4 else s


def _ms(v) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.fromisoformat(str(v)).timestamp() * 1000)
    except ValueError:
        return 0


def load(path: str | None = None, env=None) -> dict:
    p = path or (os.environ if env is None else env).get("CNQ_OWNER_OVERRIDES") or PATH
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def active(ov: dict | None, now: int | None = None) -> dict:
    """{"close": set, "no_short": set, "no_long": set, "set": ms, "until": ms, "note_ru"} — empty sets once «until» has passed"""
    ov = ov or {}
    now = int(now if now is not None else time.time() * 1000)
    until = _ms(ov.get("until"))
    on = bool(until) and now < until
    pick = lambda k: {base(x) for x in (ov.get(k) or []) if x} if on else set()      # noqa: E731
    # 05.11: copies the owner closed by hand on OKX (trade ids «W-…»): the desk closes them too and never re-opens them (no expiry)
    return {"close": pick("close"), "no_short": pick("no_short"), "no_long": pick("no_long"), "set": _ms(ov.get("set")),
            "until": until, "note_ru": ov.get("note_ru") or "решение владельца",
            "closed_ids": {str(x) for x in (ov.get("closed_ids") or []) if x},
            # 14.11 (owner, real money: «Давай ARB лонг Ивана поставим»): these trade ids may open through the futures day stop;
            # every other check (follow list, join window, size, caps) stays. Own expiry «day_stop_ok_until», else «until».
            "day_stop_ok": {str(x).split(":")[-1] for x in (ov.get("day_stop_ok") or []) if x}
            if now < (_ms(ov.get("day_stop_ok_until")) or until) else set()}


def day_stop_ok(a: dict, tid) -> bool:
    """the owner explicitly let this trade open while the futures day stop holds (matched by «W-…» id, desk prefix ignored)"""
    ok = a.get("day_stop_ok") or set()
    return "*" in ok or str(tid or "").split(":")[-1] in ok      # «*» (owner 14.11: «копировать все»): every copy trade until expiry


def blocked(a: dict, sym, side: str, opened=None) -> str | None:
    """why this trade (coin, LONG/SHORT, opened ms) must not be held / opened now, else None"""
    b, side = base(sym), str(side or "").upper()
    if b in a["close"] and int(opened or 0) < (a["set"] or 1 << 62):
        return f"закрыто по решению владельца: {a['note_ru']}"
    if side == "SHORT" and b in a["no_short"]:
        return f"шорты {b} запрещены владельцем: {a['note_ru']}"
    if side == "LONG" and b in a["no_long"]:
        return f"лонги {b} запрещены владельцем: {a['note_ru']}"
    return None

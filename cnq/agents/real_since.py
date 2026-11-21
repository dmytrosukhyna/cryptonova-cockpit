"""One place for «с какого дня» of the public real account (owner 21.11: the account was restarted on 21.11.2026; no public text
may claim a result from before its start). Everything is read from web/public_real.json: since (ms), label_ru («Реальный счёт
CRYPTO NOVA AI · с 21.11.2026»), start_label_ru («стартовый капитал ≈ $10 000»). The exact start_usd stays for the math only —
publicly the start is always «≈ $10 000» (rounded to the thousand). Without the file the texts say «с начала счёта», never a date."""
from __future__ import annotations

import datetime as dt
import json
import os

MONTHS = {"ru": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"),
          "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")}


def load(out: str = "web") -> dict:
    try:
        with open(os.path.join(out, "public_real.json"), encoding="utf-8") as f:
            d = json.load(f)
        return info(d if isinstance(d, dict) else {})
    except (OSError, ValueError):
        return info({})


def approx_start(v) -> str:
    """«≈ $10 000» — the start rounded to the thousand (never the exact figure in public)"""
    try:
        r = int(round(float(v) / 1000.0)) * 1000
    except (TypeError, ValueError):
        return ""
    return "≈ $" + f"{r:,}".replace(",", " ") if r > 0 else ""


def info(real: dict) -> dict:
    """{since, short «21.11», full «21.11.2026», words_ru «21 ноября 2026 года», short_en «21 Nov», label_ru, start_label_ru,
    start_approx «≈ $10 000», known} — with no date known every text falls back to «с начала счёта»"""
    real = real or {}
    try:
        since = int(real.get("since"))
    except (TypeError, ValueError):
        since = None
    out = {"since": since, "known": since is not None}
    if since is not None:
        d = dt.datetime.fromtimestamp(since / 1000, dt.timezone.utc)
        out.update(short=f"{d:%d.%m}", full=f"{d:%d.%m.%Y}", words_ru=f"{d.day} {MONTHS['ru'][d.month - 1]} {d.year} года",
                   short_en=f"{d.day} {MONTHS['en'][d.month - 1]}")
    ap = approx_start(real.get("start_usd"))
    out["start_approx"] = ap
    out["label_ru"] = real.get("label_ru") or ("Реальный счёт CRYPTO NOVA AI · с " + out["full"] if since is not None else "Реальный счёт CRYPTO NOVA AI")
    out["start_label_ru"] = real.get("start_label_ru") or (f"стартовый капитал {ap}" if ap else "")
    return out


def since_ru(i: dict, full: bool = False) -> str:
    """«с 21.11» / «с 21.11.2026» — or «с начала счёта» when the date is not known"""
    return ("с " + (i["full"] if full else i["short"])) if i.get("known") else "с начала счёта"

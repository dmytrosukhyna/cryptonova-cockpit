"""«Настроение опционов» — an INFORMATION tile, not a desk and not a signal (owner 30.10.2026, docs/research/options-2026-10-30.md,
variant «в»: CRYPTO NOVA does not trade options — none on OKX Europe, none in copy trading, no gain on 2023–2026 history).

What it reads, from public APIs, stdlib only:
  • DVOL — Deribit's 30-day implied-volatility index for BTC (what the options market expects BTC to move, % a year), its daily
    closes for the last year → where today's value sits among them (percentile: 0 = the calmest day of the year, 100 = the most
    frightened);
  • put / call by open interest — all BTC options on Deribit: puts (insurance against a fall) divided by calls (bets on a rise).
Fallback when Deribit does not answer: OKX /api/v5/public/opt-summary (mark IV of the ≈30-day near-the-money options) and
/api/v5/public/open-interest (OI per option) — `source` says which one was used.

Reading (label): «спокойно» — DVOL in the lower third of the year and no excess of puts; «напряжённо» — the middle, or puts
piling up; «страх» — DVOL in the top third of the year (or far above it) or puts clearly outnumbering calls. Expensive puts are
fear and often appear near a bottom; cheap puts in a rising market are complacency. Nothing here opens or closes a trade.

Cache: `data/options_mood.json` — the daily DVOL closes (so the percentile survives an API hiccup) and the last answer; a run
younger than CACHE_MIN minutes is reused as is (the pipeline step runs every ~30 min)."""
from __future__ import annotations

import json
import os
import statistics
import time
import urllib.request

DERIBIT = "https://www.deribit.com/api/v2/public/"
OKX = "https://www.okx.com/api/v5/public/"
CACHE_MIN = 25
DAY_MS = 86_400_000
YEAR_DAYS = 365
MIN_HIST = 60                 # fewer daily closes than this → no percentile (the first weeks after a fresh cache)
STATE_FILE = "options_mood.json"


def _get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "cnq-options-mood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------------------------------------- Deribit (primary)
def fetch_deribit(now: int, get=_get) -> dict:
    """{'dvol': float, 'hist': {day_ms: close}, 'puts': BTC, 'calls': BTC} — raises on any missing piece."""
    dvol = float(get(DERIBIT + "get_index_price?index_name=btcdvol_usdc")["result"]["index_price"])
    hist = {}
    try:
        rows = get(DERIBIT + f"get_volatility_index_data?currency=BTC&resolution=1D&start_timestamp={now - (YEAR_DAYS + 5) * DAY_MS}"
                   f"&end_timestamp={now}")["result"]["data"]
        for row in rows:                                   # [timestamp, open, high, low, close]
            if len(row) >= 5 and row[4]:
                hist[int(row[0]) // DAY_MS * DAY_MS] = float(row[4])
    except Exception:  # noqa: BLE001 — the daily history is a nicety; today's DVOL and the cache carry the percentile
        pass
    puts = calls = 0.0
    for r in get(DERIBIT + "get_book_summary_by_currency?currency=BTC&kind=option")["result"]:
        name = str(r.get("instrument_name") or "")
        oi = float(r.get("open_interest") or 0)
        if name.endswith("-P"):
            puts += oi
        elif name.endswith("-C"):
            calls += oi
    if calls <= 0:
        raise ValueError("no call open interest")
    return {"dvol": dvol, "hist": hist, "puts": puts, "calls": calls, "source": "deribit"}


# ---------------------------------------------------------------------------------------------------------- OKX (fallback)
def _okx_expiry_days(inst: str, now: int) -> float | None:
    """BTC-USD-261226-80000-P → days to 26.12.2026 08:00 UTC."""
    parts = inst.split("-")
    if len(parts) != 5:
        return None
    try:
        from datetime import datetime, timezone
        exp = datetime.strptime(parts[2], "%y%m%d").replace(hour=8, tzinfo=timezone.utc)
    except ValueError:
        return None
    return (exp.timestamp() * 1000 - now) / DAY_MS


def fetch_okx(now: int, get=_get) -> dict:
    """IV ≈ median mark volatility of the near-the-money (|delta| 0.35–0.65) options 20–45 days out; OI from the open-interest
    endpoint (contracts: 0.01 BTC each on OKX, the ratio does not care)."""
    rows = get(OKX + "opt-summary?uly=BTC-USD").get("data") or []
    ivs = []
    for r in rows:
        days = _okx_expiry_days(str(r.get("instId") or ""), now)
        try:
            delta, vol = abs(float(r.get("delta") or 0)), float(r.get("markVol") or 0)
        except ValueError:
            continue
        if days is not None and 20 <= days <= 45 and 0.35 <= delta <= 0.65 and vol > 0:
            ivs.append(vol * 100)
    if not ivs:
        raise ValueError("no near-the-money 30-day options")
    puts = calls = 0.0
    for r in get(OKX + "open-interest?instType=OPTION&instFamily=BTC-USD").get("data") or []:
        inst, oi = str(r.get("instId") or ""), float(r.get("oi") or 0)
        if inst.endswith("-P"):
            puts += oi
        elif inst.endswith("-C"):
            calls += oi
    if calls <= 0:
        raise ValueError("no call open interest")
    return {"dvol": round(statistics.median(ivs), 2), "hist": {}, "puts": puts, "calls": calls, "source": "okx"}


# ---------------------------------------------------------------------------------------------------------- reading
def percentile(value: float, hist: list[float]) -> int | None:
    if len(hist) < MIN_HIST:
        return None
    below = sum(1 for h in hist if h < value)
    equal = sum(1 for h in hist if h == value)
    return int(round(100 * (below + equal / 2) / len(hist)))


def label(dvol: float | None, pct: int | None, put_call: float | None) -> str:
    """'calm' / 'tense' / 'fear' — explained in the module docstring; without a percentile DVOL itself decides (BTC DVOL lived
    between ≈35 and ≈90 in 2023–2026: below 45 calm, above 65 fear)."""
    if dvol is None:
        return "tense" if put_call is not None and put_call >= 0.7 else "calm"
    hi = pct >= 67 if pct is not None else dvol >= 65
    lo = pct <= 33 if pct is not None else dvol < 45
    if hi or (put_call is not None and put_call >= 0.9):
        return "fear"
    if lo and (put_call is None or put_call < 0.7):
        return "calm"
    return "tense"


LABELS = {"calm": ("спокойно", "calm"), "tense": ("напряжённо", "tense"), "fear": ("страх", "fear")}


def _text_ru(dvol, pct, put_call, src) -> str:
    bits = []
    if dvol is not None:
        s = f"Рынок опционов ждёт от BTC ход около {dvol:.0f}% в год (DVOL)"
        if pct is not None:
            s += f" — это {'дороже' if pct >= 50 else 'дешевле'}, чем в {pct if pct >= 50 else 100 - pct}% дней за последний год"
        bits.append(s + ".")
    if put_call is not None:
        bits.append(f"Путов (страховок от падения) к коллам по открытому интересу {put_call:.2f}"
                    + (" — страховку берут заметно чаще обычного." if put_call >= 0.9 else " — путов больше обычного." if put_call >= 0.7 else "."))
    bits.append("Это информация о настроении, не сигнал: CRYPTO NOVA опционами не торгует."
                + (" Источник: OKX (Deribit не ответил)." if src == "okx" else ""))
    return " ".join(bits)


def build(now: int, data: dict | None, state: dict, error: str | None) -> dict:
    hist = {str(k): v for k, v in (state.get("hist") or {}).items()}
    if data:
        for k, v in (data.get("hist") or {}).items():
            hist[str(k)] = v
        if data.get("source") == "deribit":
            hist[str(now // DAY_MS * DAY_MS)] = data["dvol"]       # today's point, overwritten by the next run
    cutoff = now - YEAR_DAYS * DAY_MS
    hist = {k: v for k, v in hist.items() if int(k) >= cutoff}
    state["hist"] = hist
    dvol = data["dvol"] if data else None
    pct = percentile(dvol, list(hist.values())) if dvol is not None and data.get("source") == "deribit" else None
    put_call = round(data["puts"] / data["calls"], 3) if data else None
    lab = label(dvol, pct, put_call) if data else None
    doc = {"asof": now, "name": "Настроение опционов", "source": data["source"] if data else None, "error": error,
           "dvol": round(dvol, 2) if dvol is not None else None, "dvol_pct_1y": pct, "dvol_hist_days": len(hist),
           "put_call_oi": put_call, "puts_oi": round(data["puts"], 1) if data else None, "calls_oi": round(data["calls"], 1) if data else None,
           "label": lab, "label_ru": LABELS[lab][0] if lab else None, "label_en": LABELS[lab][1] if lab else None,
           "text_ru": _text_ru(dvol, pct, put_call, data["source"]) if data else None,
           "note_ru": "информация, не сигнал", "note_en": "information, not a signal",
           "rules_ru": ("DVOL — индекс ожидаемой волатильности BTC на 30 дней (Deribit); процентиль — место сегодняшнего значения среди "
                        "дневных значений за год. Пут/колл — открытый интерес путов к коллам по всем опционам BTC. «Спокойно» — DVOL в "
                        "нижней трети года и путов не больше обычного; «напряжённо» — середина или путов больше 0.7 на колл; «страх» — "
                        "DVOL в верхней трети года или путов от 0.9 на колл. Дорогие путы — страх, часто рядом с дном; дешёвые путы на "
                        "растущем рынке — самоуспокоенность. Ничего из этого не открывает и не закрывает сделки."),
           "lesson": "l4-options"}
    return doc


def run(out: str = "web", data_dir: str = "data", now: int | None = None, fetchers=None, force: bool = False) -> dict:
    now = now or int(time.time() * 1000)
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, STATE_FILE)
    try:
        state = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        state = {}
    last = state.get("last")
    if last and not force and last.get("source") and now - int(last.get("asof") or 0) < CACHE_MIN * 60_000:
        return last
    data, errors = None, []
    for fetch in fetchers or (fetch_deribit, fetch_okx):
        try:
            data = fetch(now)
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{getattr(fetch, '__name__', 'fetch')}: {type(e).__name__}: {str(e)[:80]}")
    doc = build(now, data, state, "; ".join(errors) or None)
    if not data and last:                                  # nothing answered: keep the last reading, say how old it is
        doc = dict(last, error=doc["error"], stale=True)
    state["last"] = doc
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.makedirs(out, exist_ok=True)
    tmp = os.path.join(out, "options_mood.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(out, "options_mood.json"))
    return doc

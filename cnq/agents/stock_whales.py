"""«Киты фондового рынка» for the VIP panel (04.11.2026, owner: «Будем отслеживать топов» — крипто и фондовый рынок).

Free official data only, information only (no orders, no desk):
  * SEC EDGAR 13F-HR — quarterly holdings of 10 famous funds (data.sec.gov submissions JSON → the filing's information table XML).
    For every fund: top holdings, new positions / adds / cuts / exits against the previous quarter, filing date and period.
    13F comes up to 45 days after the quarter ends, so this is the funds' portfolio of 1–4 months ago, not today's.
  * «Консенсус» — stocks that ≥ 2 of these funds bought (new or added ≥ 5 % shares) in the same quarter.
  * ARK Invest (Cathie Wood) — the funds' daily holdings CSVs; the day's buys and sells are the change of shares against the
    previous file, cleaned of the whole-fund creations/redemptions (median change of all positions).
  * US Congress trades — the public «stock watcher» JSON files, if they answer; otherwise the block says the source is down.

SEC asks for a descriptive User-Agent and ≤ 10 requests a second: UA below (generic, no personal email), ≥ 0.15 s between calls.
Tickers: 13F lists CUSIPs, not tickers — OpenFIGI's free mapping API (no key: ≤ 25 requests a minute, 10 CUSIPs each),
cached for good in the state database; without a ticker the issuer's name is shown.
Checked once a day (SEC), every 6 h (ARK) — the pipeline calls this every 30 min and it returns at once in between.
Stdlib only. A source that fails is reported as «источник недоступен» in the feed; nothing here raises to the caller.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sqlite3
import statistics as st
import time
import urllib.request
import xml.etree.ElementTree as ET

UA = "CryptoNovaResearch/1.0 (+https://cryptonova.vip)"
H, DAY = 3_600_000, 86_400_000
SEC_EVERY = 20 * H                      # «13F daily check» (the pipeline runs every 30 min; this keeps it to ~1 a day)
ARK_EVERY = 6 * H
CONGRESS_EVERY = 12 * H
KEEP_FILINGS = 3                        # 3 latest 13F-HR per fund → the diff of the latest and of the quarter before it
CHG_MIN = 0.05                          # «добавил» / «сократил» = shares changed by ≥ 5 %
TOP_N, LIST_N = 15, 12
FIGI_PER_RUN = 12                       # OpenFIGI requests per run (10 CUSIPs each), 2.6 s apart
FIGI_RETRY_MS = 30 * DAY

# 10 funds the owner named (04.11). CIK = SEC's id; the fund's legal name in the feed comes from SEC itself.
FUNDS = [
    {"key": "berkshire", "cik": 1067983, "name_ru": "Berkshire Hathaway", "who_ru": "Уоррен Баффетт"},
    {"key": "scion", "cik": 1649339, "name_ru": "Scion Asset Management", "who_ru": "Майкл Бьюрри"},
    {"key": "pershing", "cik": 1336528, "name_ru": "Pershing Square", "who_ru": "Билл Экман"},
    {"key": "bridgewater", "cik": 1350694, "name_ru": "Bridgewater Associates", "who_ru": "Рэй Далио (основатель)"},
    {"key": "renaissance", "cik": 1037389, "name_ru": "Renaissance Technologies", "who_ru": "Джим Саймонс (основатель), квант-фонд"},
    {"key": "appaloosa", "cik": 1656456, "name_ru": "Appaloosa", "who_ru": "Дэвид Теппер"},
    {"key": "duquesne", "cik": 1536411, "name_ru": "Duquesne Family Office", "who_ru": "Стэнли Дракенмиллер"},
    {"key": "soros", "cik": 1029160, "name_ru": "Soros Fund Management", "who_ru": "Джордж Сорос"},
    {"key": "tiger", "cik": 1167483, "name_ru": "Tiger Global", "who_ru": "Чейз Коулман"},
    {"key": "coatue", "cik": 1135730, "name_ru": "Coatue Management", "who_ru": "Филипп Лаффонт"},
]

ARK_BASE = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/"
ARK_FUNDS = {"ARKK": "ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv", "ARKW": "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv",
             "ARKQ": "ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ_HOLDINGS.csv", "ARKG": "ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv",
             "ARKF": "ARK_BLOCKCHAIN_&_FINTECH_INNOVATION_ETF_ARKF_HOLDINGS.csv", "ARKX": "ARK_SPACE_EXPLORATION_&_INNOVATION_ETF_ARKX_HOLDINGS.csv"}
ARK_MOVE = 0.02                         # a position whose shares moved ≥ 2 % beyond the whole fund's move = a trade of the day

CONGRESS = [("house", "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"),
            ("senate", "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sw_filing (cik INTEGER, acc TEXT PRIMARY KEY, form TEXT, filed TEXT, period TEXT, rows TEXT, t INTEGER);
CREATE TABLE IF NOT EXISTS sw_ticker (cusip TEXT PRIMARY KEY, ticker TEXT, t INTEGER);
CREATE TABLE IF NOT EXISTS sw_ark (fund TEXT, date TEXT, rows TEXT, PRIMARY KEY (fund, date));
CREATE TABLE IF NOT EXISTS sw_kv (k TEXT PRIMARY KEY, v TEXT);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def kv_get(conn, k, default=None):
    r = conn.execute("SELECT v FROM sw_kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(conn, k, v) -> None:
    conn.execute("INSERT OR REPLACE INTO sw_kv VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))


def _f(x, d=0.0):
    try:
        return float(str(x).replace(",", "").replace("$", "").replace("%", "").strip())
    except Exception:  # noqa: BLE001
        return d


def http(url: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 30) -> bytes:
    """GET (or POST with body) → raw bytes, gunzipped. Raises on HTTP errors (the callers turn that into «источник недоступен»)."""
    h = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate", "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding", "") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    return raw


def _sleep(s: float) -> None:
    time.sleep(s)


# ------------------------------------------------------------------------------------------------------------ 13F
def filings_13f(sub: dict, n: int = KEEP_FILINGS) -> list[dict]:
    """the n newest original 13F-HR filings (amendments «13F-HR/A» are skipped) of a submissions JSON, newest first"""
    rec = ((sub or {}).get("filings") or {}).get("recent") or {}
    out = []
    for i, form in enumerate(rec.get("form") or []):
        if form != "13F-HR":
            continue
        out.append({"acc": rec["accessionNumber"][i], "filed": rec["filingDate"][i], "period": (rec.get("reportDate") or [""] * (i + 1))[i],
                    "form": form})
    out.sort(key=lambda x: (x["period"], x["filed"]), reverse=True)
    seen, res = set(), []
    for x in out:                       # one filing per period (a second original for the same quarter: keep the newer)
        if x["period"] in seen:
            continue
        seen.add(x["period"])
        res.append(x)
    return res[:n]


def infotable_name(index_json: dict) -> str | None:
    """the information-table XML of a 13F filing's directory listing (not the cover page primary_doc.xml)"""
    items = ((index_json or {}).get("directory") or {}).get("item") or []
    xmls = [it for it in items if str(it.get("name", "")).lower().endswith(".xml") and "primary_doc" not in str(it.get("name", "")).lower()]
    if not xmls:
        return None
    for it in xmls:
        if "info" in it["name"].lower():
            return it["name"]
    return max(xmls, key=lambda it: _f(it.get("size"), 0))["name"]


def parse_infotable(raw: bytes, dollars: bool = True) -> list[dict]:
    """13F information table XML → rows {cusip, name, cls, value ($), shares, kind (SH/PRN), put_call}"""
    root = ET.fromstring(raw)
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "infoTable":
            continue
        d = {}
        for ch in el.iter():
            tag = ch.tag.split("}")[-1]
            if ch.text and ch.text.strip():
                d[tag] = ch.text.strip()
        val = _f(d.get("value"))
        out.append({"cusip": (d.get("cusip") or "").upper(), "name": d.get("nameOfIssuer") or "", "cls": d.get("titleOfClass") or "",
                    "value": val if dollars else val * 1000, "shares": _f(d.get("sshPrnamt")), "kind": d.get("sshPrnamtType") or "SH",
                    "put_call": (d.get("putCall") or "").capitalize()})
    return out


def aggregate(rows: list[dict]) -> dict[str, list]:
    """per CUSIP (shares only — puts/calls are bets, not holdings): {cusip: [name, cls, value, shares]}"""
    agg: dict[str, list] = {}
    for r in rows:
        if r["put_call"] or not r["cusip"]:
            continue
        a = agg.setdefault(r["cusip"], [r["name"], r["cls"], 0.0, 0.0])
        a[2] += r["value"]; a[3] += r["shares"]
    return {k: [v[0], v[1], round(v[2]), round(v[3], 2)] for k, v in agg.items()}


def options_count(rows: list[dict]) -> int:
    return sum(1 for r in rows if r["put_call"])


def diff(cur: dict, prev: dict) -> dict:
    """quarter against quarter: new / add / cut / exit, each sorted by the dollar size of the move"""
    new, add, cut, ext = [], [], [], []
    for c, (name, cls, val, sh) in cur.items():
        p = prev.get(c)
        px = val / sh if sh else 0.0
        if not p or p[3] <= 0:
            new.append({"cusip": c, "name": name, "cls": cls, "value": val, "shares": sh, "usd": val})
            continue
        ch = sh / p[3] - 1
        row = {"cusip": c, "name": name, "cls": cls, "value": val, "shares": sh, "prev_shares": p[3], "chg_pct": round(ch * 100, 1),
               "usd": round((sh - p[3]) * px)}
        if ch >= CHG_MIN:
            add.append(row)
        elif ch <= -CHG_MIN:
            cut.append(row)
    for c, (name, cls, val, sh) in prev.items():
        if c not in cur:
            ext.append({"cusip": c, "name": name, "cls": cls, "prev_value": val, "prev_shares": sh, "usd": -val})
    new.sort(key=lambda r: -r["usd"]); add.sort(key=lambda r: -r["usd"])
    cut.sort(key=lambda r: r["usd"]); ext.sort(key=lambda r: r["usd"])
    return {"new": new, "add": add, "cut": cut, "exit": ext}


def top_holdings(cur: dict, prev: dict | None, n: int = TOP_N) -> tuple[list[dict], float]:
    total = sum(v[2] for v in cur.values())
    rows = []
    for c, (name, cls, val, sh) in sorted(cur.items(), key=lambda kv: -kv[1][2])[:n]:
        p = (prev or {}).get(c)
        rows.append({"cusip": c, "name": name, "cls": cls, "value": val, "shares": sh, "pct": round(val / total * 100, 2) if total else None,
                     "chg_pct": (round((sh / p[3] - 1) * 100, 1) if p and p[3] else None) if prev is not None else None,
                     "is_new": prev is not None and not p})
    return rows, total


def period_label(period: str) -> str:
    """«2026-09-30» → «3 кв. 2026»"""
    m = re.match(r"(\d{4})-(\d{2})-\d{2}", period or "")
    if not m:
        return period or "—"
    q = (int(m.group(2)) - 1) // 3 + 1
    return f"{q} кв. {m.group(1)}"


def consensus(funds: list[dict], period: str) -> list[dict]:
    """stocks that ≥ 2 funds bought (new or added) in the quarter `period` — by the number of funds, then the dollars"""
    by: dict[str, dict] = {}
    for f in funds:
        d = (f.get("diffs") or {}).get(period)
        if not d:
            continue
        for kind in ("new", "add"):
            for r in d.get(kind) or []:
                x = by.setdefault(r["cusip"], {"cusip": r["cusip"], "name": r["name"], "cls": r.get("cls"), "funds": [], "usd": 0.0})
                x["funds"].append({"key": f["key"], "name": f["name_ru"], "action": kind, "usd": r["usd"], "chg_pct": r.get("chg_pct")})
                x["usd"] += r["usd"]
    out = [x for x in by.values() if len(x["funds"]) >= 2]
    for x in out:
        x["n"] = len(x["funds"]); x["usd"] = round(x["usd"])
    out.sort(key=lambda x: (-x["n"], -x["usd"]))
    return out


def sec_refresh(conn, now: int, get=http, notes: list | None = None) -> dict:
    """the 13F part: per fund the newest filings (cached), their diffs, status per fund"""
    notes = notes if notes is not None else []
    funds_out, ok_n = [], 0
    for f in FUNDS:
        cik = f["cik"]
        row = {"key": f["key"], "cik": cik, "name_ru": f["name_ru"], "who_ru": f["who_ru"],
               "link": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=13F-HR&dateb=&owner=include&count=40"}
        try:
            sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
            _sleep(0.15)
            row["sec_name"] = sub.get("name") or ""
            fl = filings_13f(sub)
            if not fl:
                raise RuntimeError("нет отчётов 13F-HR")
            for x in fl:
                if conn.execute("SELECT 1 FROM sw_filing WHERE acc=?", (x["acc"],)).fetchone():
                    continue
                accn = x["acc"].replace("-", "")
                idx = json.loads(get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/index.json"))
                _sleep(0.15)
                nm = infotable_name(idx)
                if not nm:
                    raise RuntimeError(f"в отчёте {x['acc']} нет таблицы позиций")
                raw = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{nm}", timeout=90)
                _sleep(0.15)
                rows = parse_infotable(raw, dollars=x["filed"] >= "2023-01-03")
                conn.execute("INSERT OR REPLACE INTO sw_filing VALUES (?,?,?,?,?,?,?)",
                             (cik, x["acc"], x["form"], x["filed"], x["period"],
                              json.dumps({"agg": aggregate(rows), "options": options_count(rows)}, separators=(",", ":")), now))
                conn.commit()
            keep = {x["acc"] for x in fl}
            for (acc,) in conn.execute("SELECT acc FROM sw_filing WHERE cik=?", (cik,)).fetchall():
                if acc not in keep:
                    conn.execute("DELETE FROM sw_filing WHERE acc=?", (acc,))
            conn.commit()
            row["ok"] = True
            ok_n += 1
        except Exception as e:  # noqa: BLE001
            row["ok"] = False
            row["error_ru"] = f"источник недоступен: {str(e)[:120]}"
            notes.append(f"13F {f['name_ru']}: {str(e)[:120]}")
        funds_out.append(row)
    return {"funds": funds_out, "ok": ok_n}


def fix_units(agg: dict) -> tuple[dict, bool]:
    """04.11 (seen in Duquesne's 13F): some filers still report «value» in thousands although SEC asks for dollars since 2023 —
    the median price per share then looks like $0.05. Below $0.5 a share the whole table is scaled ×1000."""
    px = [v[2] / v[3] for v in agg.values() if v[3] > 0 and v[2] > 0]
    if px and st.median(px) < 0.5:
        return {k: [v[0], v[1], v[2] * 1000, v[3]] for k, v in agg.items()}, True
    return agg, False


def build_funds(conn, status: dict | None = None, now: int | None = None) -> list[dict]:
    """per fund from the cached filings: latest quarter, top holdings, the moves of the latest quarter (and of the one before)"""
    st_by = {x["key"]: x for x in (status or {}).get("funds") or []}
    out = []
    for f in FUNDS:
        rows = conn.execute("SELECT acc, filed, period, rows FROM sw_filing WHERE cik=? ORDER BY period DESC, filed DESC", (f["cik"],)).fetchall()
        s = st_by.get(f["key"], {})
        o = {"key": f["key"], "cik": f["cik"], "name_ru": f["name_ru"], "who_ru": f["who_ru"], "sec_name": s.get("sec_name"),
             "link": s.get("link") or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={f['cik']:010d}&type=13F-HR",
             "ok": s.get("ok", bool(rows)), "error_ru": s.get("error_ru")}
        if not rows:
            o["error_ru"] = o.get("error_ru") or "источник недоступен: отчётов ещё нет"
            out.append(o)
            continue
        fl = [{"acc": a, "filed": fd, "period": p, **json.loads(r)} for a, fd, p, r in rows]
        scaled = False
        for x in fl:
            x["agg"], sc = fix_units(x["agg"])
            scaled = scaled or sc
        cur, prev = fl[0], (fl[1] if len(fl) > 1 else None)
        top, total = top_holdings(cur["agg"], prev["agg"] if prev else None)
        o.update(filed=cur["filed"], period=cur["period"], period_ru=period_label(cur["period"]), acc=cur["acc"],
                 filing_link=f"https://www.sec.gov/Archives/edgar/data/{f['cik']}/{cur['acc'].replace('-', '')}/",
                 prev_period=prev["period"] if prev else None, prev_period_ru=period_label(prev["period"]) if prev else None,
                 total_value=round(total), positions=len(cur["agg"]), options=cur.get("options", 0), top=top, diffs={},
                 units_fixed=scaled)
        try:                               # a fund that stopped filing (Scion: last 13F for 3 кв. 2025) is marked, not hidden
            age_d = ((now or int(time.time() * 1000)) - int(time.mktime(time.strptime(cur["period"], "%Y-%m-%d")) * 1000)) / DAY
        except Exception:  # noqa: BLE001
            age_d = 0
        if age_d > 200:
            o["stale_ru"] = f"последний отчёт за {period_label(cur['period'])} — фонд больше не подавал 13F"
        for i in range(len(fl) - 1):
            o["diffs"][fl[i]["period"]] = diff(fl[i]["agg"], fl[i + 1]["agg"])
        d = o["diffs"].get(cur["period"])
        if d:
            o["moves"] = {k: v[:LIST_N] for k, v in d.items()}
            o["moves_n"] = {k: len(v) for k, v in d.items()}
        out.append(o)
    return out


def figi_map(cusips: list[str], post=http) -> dict[str, str]:
    """OpenFIGI: CUSIP → US ticker (≤ 10 per request). Missing → «»."""
    body = json.dumps([{"idType": "ID_CUSIP", "idValue": c} for c in cusips]).encode()
    ans = json.loads(post("https://api.openfigi.com/v3/mapping", body=body, headers={"Content-Type": "application/json"}))
    out = {}
    for c, a in zip(cusips, ans if isinstance(ans, list) else []):
        data = (a or {}).get("data") or []
        us = [x for x in data if x.get("exchCode") == "US"] or data
        out[c] = (us[0].get("ticker") or "") if us else ""
    return out


def tickers(conn, cusips: list[str], now: int, post=http, notes: list | None = None, budget: int = FIGI_PER_RUN) -> dict[str, str]:
    known = {}
    for c, t, ts in conn.execute("SELECT cusip, ticker, t FROM sw_ticker").fetchall():
        known[c] = (t, ts)
    need = [c for c in dict.fromkeys(cusips) if c and (c not in known or (not known[c][0] and now - known[c][1] > FIGI_RETRY_MS))]
    for i in range(0, min(len(need), budget * 10), 10):
        part = need[i:i + 10]
        try:
            m = figi_map(part, post)
        except Exception as e:  # noqa: BLE001
            if notes is not None:
                notes.append(f"OpenFIGI: {str(e)[:100]}")
            break
        for c in part:
            known[c] = (m.get(c, ""), now)
            conn.execute("INSERT OR REPLACE INTO sw_ticker VALUES (?,?,?)", (c, m.get(c, ""), now))
        conn.commit()
        _sleep(2.6)
    return {c: v[0] for c, v in known.items() if v[0]}


# ------------------------------------------------------------------------------------------------------------ ARK
def parse_ark_csv(raw: bytes) -> tuple[str | None, list[dict]]:
    """ARK holdings CSV → (date «YYYY-MM-DD», rows {ticker, company, cusip, shares, value, weight})"""
    txt = raw.decode("utf-8-sig", errors="replace")
    rows, date = [], None
    for r in csv.DictReader(io.StringIO(txt)):
        r = {str(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if not r.get("fund") or not (r.get("ticker") or r.get("cusip")) or not r.get("shares"):
            continue
        d = r.get("date") or ""
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d)
        if m:
            d = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        date = date or d
        val = next((r[k] for k in r if k.startswith("market value")), "")
        wt = next((r[k] for k in r if k.startswith("weight")), "")
        rows.append({"ticker": r.get("ticker") or "", "company": r.get("company") or "", "cusip": r.get("cusip") or "",
                     "shares": _f(r.get("shares")), "value": _f(val), "weight": _f(wt)})
    return date, rows


def ark_moves(cur: list[dict], prev: list[dict], fund: str) -> list[dict]:
    """the day's trades: positions whose shares moved beyond the fund's own creation/redemption (median move of all), new, exits"""
    pk = {(r["ticker"] or r["cusip"]): r for r in prev}
    ck = {(r["ticker"] or r["cusip"]): r for r in cur}
    ratios = [ck[k]["shares"] / pk[k]["shares"] for k in ck if k in pk and pk[k]["shares"] > 0 and ck[k]["shares"] > 0]
    base = st.median(ratios) if ratios else 1.0
    out = []
    for k, r in ck.items():
        p = pk.get(k)
        px = r["value"] / r["shares"] if r["shares"] else 0.0
        if not p:
            out.append({"fund": fund, "ticker": r["ticker"], "company": r["company"], "action": "new", "shares": r["shares"], "usd": round(r["value"])})
            continue
        if p["shares"] <= 0:
            continue
        rel = r["shares"] / p["shares"] / base - 1
        if abs(rel) >= ARK_MOVE:
            dsh = r["shares"] - p["shares"] * base
            out.append({"fund": fund, "ticker": r["ticker"], "company": r["company"], "action": "buy" if rel > 0 else "sell",
                        "shares": round(dsh), "chg_pct": round(rel * 100, 1), "usd": round(dsh * px), "weight": r["weight"]})
    for k, p in pk.items():
        if k not in ck:
            out.append({"fund": fund, "ticker": p["ticker"], "company": p["company"], "action": "exit", "shares": -p["shares"], "usd": -round(p["value"])})
    out.sort(key=lambda x: -abs(x["usd"]))
    return out


def ark_refresh(conn, now: int, get=http, notes: list | None = None) -> dict:
    ok, errs = 0, []
    for fund, fn in ARK_FUNDS.items():
        try:
            date, rows = parse_ark_csv(get(ARK_BASE + urllib.request.quote(fn), timeout=30))
            if not rows or not date:
                raise RuntimeError("пустой файл")
            conn.execute("INSERT OR REPLACE INTO sw_ark VALUES (?,?,?)", (fund, date, json.dumps(rows, separators=(",", ":"))))
            for (d,) in conn.execute("SELECT date FROM sw_ark WHERE fund=? ORDER BY date DESC LIMIT -1 OFFSET 10", (fund,)).fetchall():
                conn.execute("DELETE FROM sw_ark WHERE fund=? AND date=?", (fund, d))
            ok += 1
        except Exception as e:  # noqa: BLE001
            errs.append(f"{fund}: {str(e)[:80]}")
        _sleep(0.5)
    conn.commit()
    if errs and notes is not None:
        notes.append("ARK: " + "; ".join(errs[:3]))
    return {"ok": ok, "errors": errs}


def build_ark(conn) -> dict:
    funds, moves, last = [], [], None
    for fund in ARK_FUNDS:
        rows = conn.execute("SELECT date, rows FROM sw_ark WHERE fund=? ORDER BY date DESC LIMIT 2", (fund,)).fetchall()
        if not rows:
            continue
        cur = json.loads(rows[0][1])
        f = {"fund": fund, "date": rows[0][0], "positions": len(cur), "value": round(sum(r["value"] for r in cur)),
             "top": sorted(cur, key=lambda r: -r["weight"])[:10], "prev_date": rows[1][0] if len(rows) > 1 else None}
        last = max(last or "", rows[0][0])
        if len(rows) > 1:
            mv = ark_moves(cur, json.loads(rows[1][1]), fund)
            for m in mv:
                m["date"] = rows[0][0]; m["prev_date"] = rows[1][0]
            f["moves_n"] = len(mv)
            moves += mv
        funds.append(f)
    moves.sort(key=lambda x: -abs(x["usd"]))
    return {"funds": funds, "moves": moves[:40], "date": last}


# ------------------------------------------------------------------------------------------------------------ Congress
def parse_congress(data, chamber: str, now: int, days: int = 45) -> list[dict]:
    out = []
    for x in data if isinstance(data, list) else []:
        disc = x.get("disclosure_date") or x.get("date_recieved") or ""
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", disc)
        if m:
            disc = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        try:
            t = int(time.mktime(time.strptime(disc[:10], "%Y-%m-%d")) * 1000)
        except Exception:  # noqa: BLE001
            continue
        if t < now - days * DAY:
            continue
        tk = (x.get("ticker") or "").strip()
        if not tk or tk in ("--", "N/A"):
            continue
        out.append({"chamber": chamber, "who": x.get("representative") or x.get("senator") or "", "ticker": tk,
                    "type": (x.get("type") or "").lower(), "amount": x.get("amount") or "", "date": x.get("transaction_date") or "",
                    "disclosed": disc[:10]})
    out.sort(key=lambda r: r["disclosed"], reverse=True)
    return out


def congress_refresh(get=http, now: int | None = None, notes: list | None = None) -> dict:
    now = now or int(time.time() * 1000)
    rows, ok, errs = [], [], []
    for ch, url in CONGRESS:
        try:
            rows += parse_congress(json.loads(get(url, timeout=60)), ch, now)
            ok.append(ch)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{ch}: {str(e)[:80]}")
    if errs and notes is not None:
        notes.append("Конгресс: " + "; ".join(errs))
    buys: dict[str, dict] = {}
    for r in rows:
        if "purchase" in r["type"]:
            b = buys.setdefault(r["ticker"], {"ticker": r["ticker"], "n": 0, "who": []})
            b["n"] += 1
            if r["who"] not in b["who"]:
                b["who"].append(r["who"])
    top = sorted(buys.values(), key=lambda b: (-len(b["who"]), -b["n"]))[:15]
    return {"available": bool(ok), "chambers": ok, "errors": errs, "recent": rows[:40], "top_buys": top,
            "note_ru": None if ok else "Бесплатный источник сделок Конгресса США (House/Senate Stock Watcher) не отвечает — блок пропущен."}


# ------------------------------------------------------------------------------------------------------------ entry point
def _ticker_fill(obj, tk: dict) -> None:
    if isinstance(obj, dict):
        if "cusip" in obj and "ticker" not in obj:
            obj["ticker"] = tk.get(obj["cusip"]) or ""
        for v in obj.values():
            _ticker_fill(v, tk)
    elif isinstance(obj, list):
        for v in obj:
            _ticker_fill(v, tk)


def run(conn: sqlite3.Connection, prev: dict | None = None, now: int | None = None, get=http, force: bool = False) -> dict | None:
    """→ the web/stock_whales.json document, or None when nothing is due (the old file stays). Never raises on a source error."""
    now = now or int(time.time() * 1000)
    ensure_schema(conn)
    prev = prev or {}
    chk = dict(prev.get("checked") or {})
    due_sec = force or now - int(chk.get("sec") or 0) >= SEC_EVERY
    due_ark = force or now - int(chk.get("ark") or 0) >= ARK_EVERY
    due_cg = force or now - int(chk.get("congress") or 0) >= CONGRESS_EVERY
    if not (due_sec or due_ark or due_cg) and prev.get("funds"):
        return None
    notes: list[str] = []
    sources = dict(prev.get("sources") or {})
    sec_status = prev.get("sec_status") or {}
    if due_sec:
        sec_status = sec_refresh(conn, now, get, notes)
        sources["sec"] = {"ok": sec_status["ok"] > 0, "n_ok": sec_status["ok"], "n": len(FUNDS), "t": now,
                          "name_ru": "SEC EDGAR 13F", "err": None if sec_status["ok"] else "источник недоступен"}
        chk["sec"] = now
    funds = build_funds(conn, sec_status, now)
    if due_ark:
        a = ark_refresh(conn, now, get, notes)
        sources["ark"] = {"ok": a["ok"] > 0, "n_ok": a["ok"], "n": len(ARK_FUNDS), "t": now, "name_ru": "ARK Invest (CSV фондов)",
                          "err": None if a["ok"] else "источник недоступен"}
        chk["ark"] = now
    ark = build_ark(conn)
    if due_cg:
        cg = congress_refresh(get, now, notes)
        sources["congress"] = {"ok": cg["available"], "t": now, "name_ru": "Сделки Конгресса США (Stock Watcher)",
                               "err": None if cg["available"] else "источник недоступен"}
        chk["congress"] = now
    else:
        cg = prev.get("congress") or {"available": False}
    # consensus per quarter: the newest quarter with ≥ 2 funds' moves, and the newest «complete» one (≥ 8 of 10 funds)
    periods: dict[str, int] = {}
    for f in funds:
        for p in (f.get("diffs") or {}):
            periods[p] = periods.get(p, 0) + 1
    cons = []
    for p in sorted(periods, reverse=True):
        if periods[p] < 2:
            continue
        if not cons or (periods[p] >= 8 and all(c["n_funds"] < 8 for c in cons)):
            cons.append({"period": p, "period_ru": period_label(p), "n_funds": periods[p],
                         "funds": [f["name_ru"] for f in funds if p in (f.get("diffs") or {})], "items": consensus(funds, p)[:20]})
        if cons and cons[-1]["n_funds"] >= 8:
            break
    # tickers for everything shown
    shown = []
    for f in funds:
        shown += [r["cusip"] for r in f.get("top") or []]
        for v in (f.get("moves") or {}).values():
            shown += [r["cusip"] for r in v]
    for c in cons:
        shown += [r["cusip"] for r in c["items"]]
    tk = tickers(conn, shown, now, get, notes) if shown else {}
    for f in funds:
        f.pop("diffs", None)
    doc = {"asof": now, "checked": chk, "sources": sources, "sec_status": sec_status,
           "funds": funds, "consensus": cons, "ark": ark, "congress": cg,
           "delay_ru": "13F — отчёт фонда за квартал. Его подают до 45 дней после конца квартала, поэтому здесь портфель 1,5–4 месяца назад, "
                       "а не сегодняшний. Шорты, опционы (кроме отметки о их числе) и не-американские акции в 13F не видны.",
           "about_ru": "Только информация, не финансовый совет. Данные — официальные отчёты SEC EDGAR (форма 13F-HR), файлы фондов ARK Invest "
                       "и раскрытия Конгресса США. «Новая» — позиции не было в прошлом квартале; «добавил» / «сократил» — число акций "
                       f"изменилось на {CHG_MIN * 100:.0f}% и больше; «вышел» — позиции больше нет. «Консенсус» — акции, которые купили "
                       "(новая позиция или докупка) 2 фонда и больше в одном квартале.",
           "notes_ru": notes[:12]}
    _ticker_fill(doc["funds"], tk)
    _ticker_fill(doc["consensus"], tk)
    return doc

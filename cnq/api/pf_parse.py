"""«Разбор портфеля» (build 45, owner 22.10): what the user holds, read from an exchange file — in memory only. Stdlib only.

  sniff(data)              → "csv" · "xlsx" · "pdf" · "image" · "xls" · None (the file's own bytes decide, not its name)
  parse_table(rows)        → holdings from a table (CSV / XLSX): deterministic, for OKX, BingX and Binance exports and alike
  parse_text(text)         → holdings from the text of a PDF statement (lines «BTC 0.5 …» under a header «Coin Total …»)
  read_csv / read_xlsx / pdf_text — the file's rows / text
  vision_holdings / llm_text_holdings — the model's reading of a screenshot / statement text (only when a model is set up)

Three table shapes are understood:
  balances   a coin column + a total (or available + frozen) column, e.g. «Coin, Total, Available, Frozen, USDT Value»
  bills      a ledger with the balance after each line + its unit (OKX «Balance» / «Balance Unit») → the newest balance per coin
  ledger     a ledger of changes (Binance «UTC_Time, Account, Operation, Coin, Change») → the sum of changes per coin
A holding = {"sym", "amount", "value_usd"?}; "value_usd" only when the file itself states a dollar value.
Nothing here writes a file or a log line with the file's content; errors carry a code, never the data."""
from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
import zlib
from xml.etree import ElementTree as ET

MAX_FILE = 10 * 1024 * 1024          # bytes of the uploaded file
MAX_ROWS = 20000                     # table rows read
MAX_HOLDINGS = 100                   # coins kept (the biggest first)
MAX_UNZIP = 40 * 1024 * 1024         # bytes inflated from an XLSX / a PDF stream in total (a zip bomb stops here)
MAX_TEXT = 200_000                   # characters of PDF text kept

SYM_RE = re.compile(r"^[A-Z0-9][A-Z0-9.]{0,14}$")
STABLES = {"USDT", "USDC", "FDUSD", "DAI", "USDE", "TUSD", "PYUSD", "USD1", "RLUSD", "BUSD", "USDD", "USDP", "USDS", "GUSD",
           "USD", "LUSD", "FRAX", "USDG", "USDQ"}
FIAT_SKIP = {"EUR", "GBP", "UAH", "RUB", "TRY", "BRL", "PLN", "KZT", "NGN", "ARS", "AUD", "CAD", "CHF", "JPY", "INR", "IDR",
             "VND", "PHP", "MXN", "ZAR", "AED", "HKD", "SGD", "KRW", "CZK", "HUF", "RON", "SEK", "NOK", "DKK", "BGN", "GEL"}
TOTAL_WORDS = {"total", "合计", "итого", "всего", "сумма", "total balance", "subtotal", "grand total", "σύνολο", "totales"}


class ParseError(Exception):
    """a code the app shows as a sentence in its language (pf_review.message): too_large, bad_type, xls, empty, no_rows,
    image_unsupported, read_failed, model_busy"""


# ------------------------------------------------------------------------------------------------ the file's type
def sniff(data: bytes) -> str | None:
    if not data:
        return None
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image"
    if data[:4] == b"\xd0\xcf\x11\xe0":
        return "xls"                         # the old binary Excel: the app asks for XLSX / CSV
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else None
        except zipfile.BadZipFile:
            return None
    head = data[:4096]
    if b"\x00" in head and not (head[:2] in (b"\xff\xfe", b"\xfe\xff")):
        return None
    return "csv"


def image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF":
        return "image/webp"
    return "image/jpeg"


# ------------------------------------------------------------------------------------------------ numbers and coins
def _clean_num(s) -> str:
    s = str(s if s is not None else "").strip().replace(" ", " ").replace(" ", " ").replace(" ", " ")
    s = s.replace("−", "-").replace("–", "-").lstrip("≈~").strip()
    s = re.sub(r"^[A-Za-z$€₮£]+\s*", "", s)                  # «$ 12.5», «USDT 3»
    s = re.sub(r"\s*[A-Za-z$€₮£%]+$", "", s)                 # «0.5 BTC», «12 USDT»
    return s.replace(" ", "").replace("'", "")


def comma_decimal(values) -> bool:
    """does this column write 0,5 for a half? — a comma followed by other than 3 digits says yes; a dot anywhere says no"""
    dot = comma = False
    for v in values:
        s = _clean_num(v)
        if re.search(r"\d,\d{1,2}$|\d,\d{4,}$|^-?0,\d", s) and "." not in s:
            comma = True
        if re.search(r"\d\.\d", s) and "," not in s:
            dot = True
        if "," in s and "." in s:
            return s.rfind(",") > s.rfind(".")
    return comma and not dot


def num(v, comma_dec: bool = False) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = _clean_num(v)
    if not s or s in ("-", "--", "—"):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if comma_dec else s.replace(",", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x == x and abs(x) != float("inf") else None


def coin(v) -> str | None:
    """«BTC» · «btc» · «BTC (Bitcoin)» · «Bitcoin (BTC)» · «LDBTC» (Binance Earn) → BTC; pairs and words → None"""
    s = str(v if v is not None else "").strip().upper()
    if not s or s.lower() in TOTAL_WORDS:
        return None
    m = re.search(r"\(([A-Z0-9]{2,15})\)", s)
    if m:
        s = m.group(1)
    s = s.split()[0] if s.split() else ""
    if "-" in s or "/" in s or "_" in s:
        return None                         # «BTC-USDT» is a pair, not a coin
    s = s.strip(".:,;")
    return s if SYM_RE.match(s) and not s.isdigit() else None


def norm_sym(s: str, known: set[str] | frozenset = frozenset()) -> str:
    """Binance Simple Earn writes LD + the coin (LDBTC); a coin that is itself known (LDO) stays"""
    if s.startswith("LD") and len(s) > 3 and s not in known and s[2:] in known:
        return s[2:]
    return s


# ------------------------------------------------------------------------------------------------ CSV / XLSX rows
def read_csv(data: bytes) -> list[list[str]]:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", errors="replace")
    else:
        for enc in ("utf-8-sig", "cp1251", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    lines = [l for l in text.splitlines()[:60] if l.strip()]
    # the delimiter that splits the most lines into the same number of columns (decimal commas don't: «0,5;1,25»)
    best = (0, 0, ",")
    for d in (",", ";", "\t", "|"):
        counts = [l.count(d) for l in lines if l.count(d)]
        if counts:
            mode = max(set(counts), key=counts.count)
            best = max(best, (counts.count(mode), mode, d))
    delim = best[2]
    rows = []
    for r in csv.reader(io.StringIO(text), delimiter=delim):
        rows.append([c.strip() for c in r])
        if len(rows) >= MAX_ROWS:
            break
    return rows


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return max(0, n - 1)


def _zread(z: zipfile.ZipFile, name: str, budget: list[int]) -> bytes:
    with z.open(name) as f:
        out = f.read(budget[0] + 1)
    budget[0] -= len(out)
    if budget[0] < 0:
        raise ParseError("read_failed")
    return out


def read_xlsx(data: bytes) -> list[list[list[str]]]:
    """every sheet of the workbook as rows of text (shared strings, inline strings, numbers as written)"""
    budget = [MAX_UNZIP]
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseError("read_failed") from None
    with z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(_zread(z, "xl/sharedStrings.xml", budget))
            for si in root.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
        sheets = sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        out = []
        for name in sheets[:10]:
            root = ET.fromstring(_zread(z, name, budget))
            rows = []
            for row in root.iter("{%s}row" % _NS["m"]):
                vals: dict[int, str] = {}
                for c in row.findall("m:c", _NS):
                    t = c.get("t")
                    v = c.find("m:v", _NS)
                    if t == "s" and v is not None and (v.text or "").isdigit():
                        i = int(v.text)
                        val = shared[i] if i < len(shared) else ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter("{%s}t" % _NS["m"]))
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    vals[_col(c.get("r") or "A")] = val.strip()
                if vals:
                    rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
                if len(rows) >= MAX_ROWS:
                    break
            out.append(rows)
        return out


# ------------------------------------------------------------------------------------------------ table → holdings
def _h(s: str) -> str:
    s = str(s or "").replace("﻿", "").strip().lower().replace("_", " ")
    return re.sub(r"\s+", " ", s)


def _base(s: str) -> str:
    return re.sub(r"\s*[(\[（].*?[)\]）]\s*", " ", _h(s)).strip()


COIN_H = {"coin", "asset", "assets", "currency", "ccy", "token", "crypto", "cryptocurrency", "coin name", "asset name",
          "монета", "валюта", "актив", "криптовалюта", "токен", "币种", "资产", "幣種", "moneda", "activo", "νόμισμα", "κρυπτονόμισμα"}
TOTAL_H = {"total", "total balance", "total amount", "balance", "equity", "amount", "qty", "quantity", "holdings", "holding",
           "total holdings", "всего", "баланс", "количество", "сумма", "итого", "总额", "余额", "数量", "总计", "cantidad", "saldo",
           "total equity", "ποσότητα", "υπόλοιπο", "position", "size"}
AVAIL_H = {"available", "available balance", "free", "avail", "доступно", "可用", "disponible", "διαθέσιμο"}
LOCKED_H = {"locked", "frozen", "in order", "in orders", "on order", "freeze", "frozen balance", "заморожено", "в ордерах", "冻结",
            "bloqueado", "δεσμευμένο"}
TIME_H = {"time", "utc time", "date", "date(utc)", "datetime", "created time", "время", "дата", "时间", "fecha", "ημερομηνία"}
CHANGE_H = {"change", "amount", "delta", "balance change", "изменение", "变动", "cambio", "μεταβολή"}
UNIT_H = {"balance unit", "balance currency", "unit"}


def _is_value_usd(h: str) -> bool:
    return bool(re.search(r"usd|usdt|\$", h)) and bool(re.search(r"valu|valuation|equiv|估值|стоимост|оценк|worth|≈|est", h))


def _find_header(rows: list[list[str]]) -> tuple[int, dict] | None:
    best = None
    for i, r in enumerate(rows[:40]):
        cols: dict = {}
        for j, cell in enumerate(r):
            h, b = _h(cell), _base(cell)
            if not h:
                continue
            if "coin" not in cols and (b in COIN_H or h in COIN_H):
                cols["coin"] = j
            elif _is_value_usd(h) and "value" not in cols:
                cols["value"] = j
            elif "btc" in h and re.search(r"valu|valuation|equiv", h):
                cols.setdefault("value_btc", j)
            elif h in UNIT_H or b in UNIT_H:
                cols.setdefault("unit", j)
            elif b in TIME_H or h in TIME_H or b.startswith("utc time") or b.startswith("time"):
                cols.setdefault("time", j)
            elif b in AVAIL_H:
                cols.setdefault("avail", j)
            elif b in LOCKED_H:
                cols.setdefault("locked", j)
            elif b in ("change", "balance change", "изменение", "变动", "cambio", "μεταβολή", "delta"):
                cols.setdefault("change", j)
            elif b in TOTAL_H and re.search(r"[(\[（]\s*(usd|usdt|\$)\s*[)\]）]", h) and "total" in cols:
                cols.setdefault("value", j)               # «Total (USDT)» next to «Total»: the dollar value
            elif b in TOTAL_H:
                if b == "amount":
                    cols.setdefault("amount", j)
                elif b == "balance":
                    cols.setdefault("balance", j)
                else:
                    cols.setdefault("total", j)
            elif b in ("operation", "type", "bill type", "trade type", "action", "операция", "тип", "类型"):
                cols.setdefault("op", j)
        if "coin" not in cols:                              # «Symbol» / «Ticker» when it holds coins, not pairs
            for j, cell in enumerate(r):
                if _base(cell) in ("symbol", "ticker", "символ", "тикер") and any(coin(x[j]) for x in rows[i + 1:i + 6] if j < len(x)):
                    cols["coin"] = j
                    break
        has_amt = any(k in cols for k in ("total", "balance", "amount", "change", "avail"))
        if ("coin" in cols or "unit" in cols) and has_amt:
            score = len(cols)
            if best is None or score > best[2]:
                best = (i, cols, score)
    return (best[0], best[1]) if best else None


_TIME_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
              "%m/%d/%Y %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%y-%m-%d %H:%M:%S")


def _time(v) -> float | None:
    s = str(v or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10,13}(\.\d+)?", s):
        x = float(s)
        return x / 1000 if x > 1e11 else x
    s = re.sub(r"\s*\(?UTC[+\-]?\d*\)?$", "", s).replace("Z", "").split(".")[0]
    for f in _TIME_FMTS:
        try:
            return dt.datetime.strptime(s, f).timestamp()
        except ValueError:
            continue
    x = num(s)
    if x is not None and 30000 < x < 80000:          # an Excel date serial
        return (x - 25569) * 86400
    return None


def exchange_of(rows: list[list[str]], cols: dict | None = None) -> str | None:
    head = " ".join(" ".join(r) for r in rows[:6]).lower()
    for name in ("okx", "bingx", "binance"):
        if name in head:
            return name
    hdr = {_h(c) for r in rows[:40] for c in r[:20]}
    if {"user id", "utc time", "operation", "change"} <= hdr:
        return "binance"
    if {"balance unit"} & hdr or {"ccy", "bill id", "bill type"} & hdr:
        return "okx"
    if {"btc valuation", "in order"} & hdr:
        return "binance"
    return None


def parse_table(rows: list[list[str]], known: set[str] | frozenset = frozenset()) -> dict:
    """{"holdings": [...], "rows": data rows seen, "skipped": rows that were not a coin, "mode", "exchange"} — no exception for
    a table without holdings: "holdings" is empty"""
    found = _find_header(rows)
    out = {"holdings": [], "rows": 0, "skipped": 0, "mode": None, "exchange": exchange_of(rows)}
    if not found:
        return out
    hi, cols = found
    data = [r for r in rows[hi + 1:] if any(str(c).strip() for c in r)]
    def colvals(k):
        j = cols.get(k)
        return [r[j] for r in data if j is not None and j < len(r)]
    dec = {k: comma_decimal(colvals(k)) for k in cols}
    def cell(r, k):
        j = cols.get(k)
        return num(r[j], dec.get(k, False)) if j is not None and j < len(r) else None
    def sym_of(r, k="coin"):
        j = cols.get(k)
        s = coin(r[j]) if j is not None and j < len(r) else None
        return norm_sym(s, known) if s else None

    agg: dict[str, dict] = {}
    if "time" in cols and "balance" in cols and ("unit" in cols or "coin" in cols):
        out["mode"] = "bills"
        newest: dict[str, tuple[float, int, float]] = {}
        for i, r in enumerate(data):
            out["rows"] += 1
            s = sym_of(r, "unit" if "unit" in cols else "coin")
            b = cell(r, "balance")
            if not s or b is None:
                out["skipped"] += 1
                continue
            t = _time(r[cols["time"]]) if cols["time"] < len(r) else None
            key = (t if t is not None else float("-inf"), -i)       # same time / no time → the line higher in the file is newer
            if s not in newest or key > newest[s][:2]:
                newest[s] = (key[0], key[1], b)
        for s, (_t, _i, b) in newest.items():
            agg[s] = {"amount": b}
    elif "change" in cols or ("time" in cols and "amount" in cols and "total" not in cols and "balance" not in cols):
        out["mode"] = "ledger"
        k = "change" if "change" in cols else "amount"
        for r in data:
            out["rows"] += 1
            s, x = sym_of(r), cell(r, k)
            if not s or x is None:
                out["skipped"] += 1
                continue
            agg.setdefault(s, {"amount": 0.0})["amount"] += x
    else:
        out["mode"] = "balances"
        k = next((c for c in ("total", "balance", "amount") if c in cols), None)
        for r in data:
            out["rows"] += 1
            s = sym_of(r)
            if k:
                x = cell(r, k)
            else:
                a, l = cell(r, "avail"), cell(r, "locked")
                x = None if a is None and l is None else (a or 0.0) + (l or 0.0)
            if not s or x is None:
                out["skipped"] += 1
                continue
            g = agg.setdefault(s, {"amount": 0.0})
            g["amount"] += x
            v = cell(r, "value")
            if v is not None and v >= 0:
                g["value_usd"] = g.get("value_usd", 0.0) + v
    out["holdings"] = finish(agg)
    return out


def finish(agg: dict[str, dict]) -> list[dict]:
    """positive amounts, fiat (other than dollars) left out, rounded, the biggest dollar values / amounts first, ≤ MAX_HOLDINGS"""
    hs = []
    for s, g in agg.items():
        a = float(g.get("amount") or 0)
        if a <= 1e-12 or s in FIAT_SKIP:
            continue
        h = {"sym": s, "amount": float(f"{a:.10g}")}
        if g.get("value_usd") is not None:
            h["value_usd"] = round(float(g["value_usd"]), 2)
        hs.append(h)
    hs.sort(key=lambda h: (-(h.get("value_usd") or 0), -h["amount"]))
    return hs[:MAX_HOLDINGS]


def best_table(tables: list[list[list[str]]], known=frozenset()) -> dict:
    res = [parse_table(t, known) for t in tables if t]
    if not res:
        return {"holdings": [], "rows": 0, "skipped": 0, "mode": None, "exchange": None}
    return max(res, key=lambda r: (len(r["holdings"]), r["rows"]))


# ------------------------------------------------------------------------------------------------ PDF → text
def _pdf_unescape(s: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(s):
        ch = s[i]
        if ch == 0x5C and i + 1 < len(s):          # backslash
            n = s[i + 1]
            m = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}.get(n)
            if m is not None:
                out.append(m); i += 2; continue
            if 48 <= n <= 55:
                j, v = i + 1, 0
                while j < len(s) and j < i + 4 and 48 <= s[j] <= 55:
                    v = v * 8 + s[j] - 48; j += 1
                out.append(v & 0xFF); i = j; continue
            if n in (10, 13):
                i += 2; continue
            out.append(n); i += 2; continue
        out.append(ch); i += 1
    return bytes(out)


def _cmap(data: bytes) -> tuple[dict[int, str], int]:
    """a ToUnicode CMap → {code: text}, code width in bytes"""
    m: dict[int, str] = {}
    width = 1
    cs = re.search(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>", data)
    if cs:
        width = max(1, len(cs.group(1)) // 2)
    def u(h: bytes) -> str:
        try:
            return bytes.fromhex(h.decode()).decode("utf-16-be", errors="ignore")
        except ValueError:
            return ""
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            m[int(a, 16)] = u(b)
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for a, b, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", blk):
            lo, hi = int(a, 16), int(b, 16)
            if hi - lo > 65535:
                continue
            if rest.startswith(b"["):
                for k, h in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", rest)):
                    m[lo + k] = u(h)
            else:
                base = bytes.fromhex(rest[1:-1].decode()) if len(rest) > 2 else b""
                for k in range(hi - lo + 1):
                    bb = bytearray(base)
                    if bb:
                        bb[-1] = (bb[-1] + k) & 0xFF
                    m[lo + k] = bytes(bb).decode("utf-16-be", errors="ignore")
    return m, width


def _decode_with(raw: bytes, cm: tuple[dict[int, str], int] | None) -> str:
    if not cm:
        return raw.decode("latin-1")
    m, w = cm
    out = []
    for i in range(0, len(raw) - w + 1, w):
        c = int.from_bytes(raw[i:i + w], "big")
        out.append(m.get(c, ""))
    return "".join(out)


_TOK = re.compile(rb"<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[-+]?\d*\.?\d+|[A-Za-z'\"*]+", re.S)


def _tokens(stream: bytes):
    """content-stream tokens; a literal string keeps its balanced inner brackets («(Value (USD))»), comments are skipped"""
    i, n = 0, len(stream)
    while i < n:
        c = stream[i]
        if c in b" \t\r\n\f\x00":
            i += 1
        elif c == 0x28:                       # (
            depth, j = 1, i + 1
            while j < n and depth:
                if stream[j] == 0x5C:
                    j += 2
                    continue
                depth += 1 if stream[j] == 0x28 else -1 if stream[j] == 0x29 else 0
                j += 1
            yield stream[i:j]
            i = j
        elif c == 0x25:                       # %
            j = stream.find(b"\n", i)
            i = n if j < 0 else j + 1
        else:
            m = _TOK.match(stream, i)
            if m:
                yield m.group(0)
                i = m.end()
            else:
                i += 1


def _content_text(stream: bytes, fonts: dict[bytes, tuple]) -> list[tuple[float, float, str]]:
    """text pieces with their position (x, y) from one content stream; Tj / TJ / ' / \" with Tf, Td, TD, Tm, T*"""
    out = []
    stack: list = []
    font = None
    x = y = 0.0
    lx = ly = 0.0
    lead = 0.0
    in_arr: list | None = None
    for t in _tokens(stream):
        if t == b"[":
            in_arr = []
            continue
        if t == b"]":
            stack.append(("arr", in_arr or []))
            in_arr = None
            continue
        if t[:1] == b"(":
            v = ("str", _pdf_unescape(t[1:-1]))
            (in_arr if in_arr is not None else stack).append(v)
            continue
        if t[:1] == b"<":
            h = re.sub(rb"\s", b"", t[1:-1])
            v = ("str", bytes.fromhex((h + b"0" * (len(h) % 2)).decode()))
            (in_arr if in_arr is not None else stack).append(v)
            continue
        if t[:1] == b"/":
            stack.append(("name", t[1:]))
            continue
        if re.fullmatch(rb"[-+]?\d*\.?\d+", t):
            if in_arr is not None:
                in_arr.append(("num", float(t)))
            else:
                stack.append(("num", float(t)))
            continue
        op = t
        nums = [v for k, v in stack if k == "num"]
        if op == b"BT":
            x = y = lx = ly = 0.0
        elif op == b"Tf":
            names = [v for k, v in stack if k == "name"]
            font = fonts.get(names[-1]) if names else None
        elif op in (b"Td", b"TD") and len(nums) >= 2:
            lx, ly = lx + nums[-2], ly + nums[-1]
            x, y = lx, ly
            if op == b"TD":
                lead = -nums[-1]
        elif op == b"Tm" and len(nums) >= 6:
            lx, ly = nums[-2], nums[-1]
            x, y = lx, ly
        elif op == b"TL" and nums:
            lead = nums[-1]
        elif op == b"T*":
            ly -= lead or 12
            x, y = lx, ly
        elif op in (b"Tj", b"'", b'"', b"TJ"):
            if op in (b"'", b'"'):
                ly -= lead or 12
                x, y = lx, ly
            parts = []
            for k, v in stack:
                if k == "str":
                    parts.append(_decode_with(v, font))
                elif k == "arr":
                    for kk, vv in v:
                        if kk == "str":
                            parts.append(_decode_with(vv, font))
                        elif kk == "num" and vv < -200:
                            parts.append(" ")
            s = "".join(parts)
            if s.strip():
                out.append((x, y, s))
                x += len(s) * 5
        stack = []
    return out


_SKIP_STREAM = (b"/Image", b"/Length1", b"/Length2", b"/Length3", b"/Type1C", b"/CIDFontType0C", b"/OpenType", b"/XRef",
                b"/Metadata", b"/EmbeddedFile", b"/ICCBased", b"/N 3", b"/N 4")
_OBJ_STREAM = re.compile(rb"(\d+)\s+\d+\s+obj\s*(<<(?:(?!\bendobj\b|\bstream\b).){0,4000}?>>)\s*stream\r?\n", re.S)


def _streams(data: bytes, budget: list[int]):
    """(object number, its dictionary, its inflated stream) for the streams that can hold text, cmaps or objects (images and
    embedded fonts are skipped without inflating them)"""
    for m in _OBJ_STREAM.finditer(data):
        start = m.end()
        dct = m.group(2)
        if any(k in dct for k in _SKIP_STREAM):
            continue
        lm = re.search(rb"/Length\s+(\d+)(?!\s+\d+\s+R)", dct)
        end = start + int(lm.group(1)) if lm else -1
        if not lm or data[end:end + 20].lstrip()[:9] != b"endstream":
            end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end]
        if b"/FlateDecode" in dct:
            try:
                raw = zlib.decompressobj().decompress(raw, max(0, budget[0]) + 1)
            except zlib.error:
                continue
            budget[0] -= len(raw)
            if budget[0] < 0:
                raise ParseError("read_failed")
        elif b"/Filter" in dct:
            continue
        yield int(m.group(1)), dct, raw


def pdf_text(data: bytes) -> str:
    """the statement's text, line by line (pypdf when the server has it, else the small reader above)"""
    try:
        from pypdf import PdfReader          # optional: better with unusual fonts
        rd = PdfReader(io.BytesIO(data))
        txt = "\n".join((p.extract_text() or "") for p in rd.pages[:30])
        if txt.strip():
            return txt[:MAX_TEXT]
    except Exception:  # noqa: BLE001 — not installed or failed: the stdlib reader
        pass
    budget = [MAX_UNZIP]
    objs: dict[int, bytes] = {}
    streams = []
    for n, dct, raw in _streams(data, budget):
        streams.append((n, dct, raw))
        if b"/ObjStm" in dct:                 # compressed objects: «num offset …» then the objects
            first = re.search(rb"/First\s+(\d+)", dct)
            if first:
                head = raw[:int(first.group(1))].split()
                pairs = [(int(head[i]), int(head[i + 1])) for i in range(0, len(head) - 1, 2)]
                body = raw[int(first.group(1)):]
                for k, (on, off) in enumerate(pairs):
                    nxt = pairs[k + 1][1] if k + 1 < len(pairs) else len(body)
                    objs[on] = body[off:nxt]
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj(.*?)endobj", data, re.S):
        objs.setdefault(int(m.group(1)), m.group(2))
    cmaps = {n: _cmap(raw) for n, dct, raw in streams if b"begincmap" in raw}
    font_cmap: dict[int, tuple] = {}
    for on, body in objs.items():
        if b"/Font" in body or b"/ToUnicode" in body:
            tu = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", body)
            if tu and int(tu.group(1)) in cmaps:
                font_cmap[on] = cmaps[int(tu.group(1))]
    fonts: dict[bytes, tuple] = {}
    for body in list(objs.values()):
        for fd in re.findall(rb"/Font\s*<<(.*?)>>", body, re.S):
            for name, ref in re.findall(rb"/([^\s/<>]+)\s+(\d+)\s+\d+\s+R", fd):
                if int(ref) in font_cmap:
                    fonts.setdefault(name, font_cmap[int(ref)])
        fr = re.search(rb"/Font\s+(\d+)\s+\d+\s+R", body)
        if fr and int(fr.group(1)) in objs:
            for name, ref in re.findall(rb"/([^\s/<>]+)\s+(\d+)\s+\d+\s+R", objs[int(fr.group(1))]):
                if int(ref) in font_cmap:
                    fonts.setdefault(name, font_cmap[int(ref)])
    lines: list[str] = []
    for n, dct, raw in streams:
        if b"/ObjStm" in dct or b"begincmap" in raw or b"BT" not in raw or b"ET" not in raw:
            continue
        pieces = _content_text(raw, fonts)
        rows: dict[int, list] = {}
        for x, y, s in pieces:
            rows.setdefault(round(y / 3), []).append((x, s))
        for ky in sorted(rows, reverse=True):
            lines.append(" ".join(s.strip() for _x, s in sorted(rows[ky])))
        if sum(len(l) for l in lines) > MAX_TEXT:
            break
    return "\n".join(lines)[:MAX_TEXT]


# ------------------------------------------------------------------------------------------------ text → holdings
_NUM_IN_TEXT = re.compile(r"(?<![\w.])[-−]?\d[\d\s,.'  ]*\d(?![\w])|(?<![\w.])\d(?![\w.,])")


def parse_text(text: str, known: set[str] | frozenset) -> dict:
    """lines of a statement: a known coin's ticker, then its numbers; a header line («Coin Total Available Frozen …») tells which
    number is the amount (the total, else the first one); dollar values when the header names them"""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    amount_idx, value_idx, header = 0, None, None
    for l in lines[:400]:
        words = [_h(w) for w in re.split(r"\s{1,}", l.strip()) if w]
        low = _h(l)
        if any(w in COIN_H for w in words) and any(k in low for k in ("total", "balance", "amount", "всего", "баланс", "количество", "available", "equity")):
            header = low
            # the order of the number columns after the coin column
            order = re.findall(r"total|balance|equity|amount|available|frozen|locked|in order|value|valuation|всего|баланс|количество|доступно|стоимость", low)
            for i, w in enumerate(order):
                if w in ("total", "balance", "equity", "amount", "всего", "баланс", "количество"):
                    amount_idx = i
                    break
            for i, w in enumerate(order):
                if w in ("value", "valuation", "стоимость"):
                    value_idx = i
            break
    agg: dict[str, dict] = {}
    seen = 0
    for l in lines:
        toks = l.replace(" ", " ").split()
        sym, at = None, -1
        for i, w in enumerate(toks[:6]):
            c = coin(w)
            if c and (c in known or c in STABLES) and not (header and _h(l) == header):
                sym, at = c, i
                break
        if not sym:
            continue
        rest = " ".join(toks[at + 1:])
        nums_raw = [m.group(0).strip() for m in re.finditer(r"[-−]?\$?\d[\d,.'  ]*", rest)]
        dec = comma_decimal(nums_raw)
        nums = [x for x in (num(n, dec) for n in nums_raw) if x is not None]
        if not nums:
            continue
        seen += 1
        a = nums[amount_idx] if amount_idx < len(nums) else nums[0]
        g = agg.setdefault(norm_sym(sym, known), {"amount": 0.0})
        g["amount"] += a
        if value_idx is not None and value_idx < len(nums):
            g["value_usd"] = g.get("value_usd", 0.0) + nums[value_idx]
    return {"holdings": finish(agg), "rows": seen, "skipped": 0, "mode": "text", "exchange": exchange_of([[l] for l in lines[:6]])}


# ------------------------------------------------------------------------------------------------ the model's reading
VISION_SYS = ("You read crypto exchange screens and statements. Return ONLY JSON: {\"holdings\": [{\"sym\": \"BTC\", \"amount\": 0.5, "
              "\"value_usd\": 41000}]}. sym = the coin's ticker in capitals; amount = the quantity the user holds (the total, not the "
              "available part); value_usd = the dollar (or USDT) value only if it is written next to the coin, else leave it out. "
              "Skip rows you cannot read for sure — never guess a number. No other text.")


def from_model(doc, known: set[str] | frozenset = frozenset()) -> list[dict]:
    items = (doc or {}).get("holdings") if isinstance(doc, dict) else doc if isinstance(doc, list) else None
    agg: dict[str, dict] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        s = coin(it.get("sym") or it.get("symbol") or it.get("coin"))
        a = num(it.get("amount"))
        if not s or a is None or a <= 0:
            continue
        g = agg.setdefault(norm_sym(s, known), {"amount": 0.0})
        g["amount"] += a
        v = num(it.get("value_usd"))
        if v is not None and v >= 0:
            g["value_usd"] = g.get("value_usd", 0.0) + v
    return finish(agg)


VISION_RE = re.compile(r"gemini|claude|llama-4|vision|gpt-4o|gpt-4\.1|gpt-5|pixtral|qwen[\w.-]*vl|gemma-3|grok-[\w.-]*vision", re.I)


def vision_holdings(cfg: dict, data: bytes, known=frozenset(), timeout: float = 25.0, opener=None) -> list[dict]:
    """cfg = {"provider", "model", "key", "url", "style"} (server.vision_config); the screenshot goes to the model as base64 and
    is not kept anywhere"""
    from cnq.agents.llm import parse_json
    b64 = base64.b64encode(data).decode()
    mime = image_mime(data)
    prompt = "Read the holdings on this exchange screenshot («Assets» / «Активы»). JSON only."
    if cfg["style"] == "anthropic":
        body = {"model": cfg["model"], "max_tokens": 2000, "temperature": 0, "system": VISION_SYS,
                "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                                                          {"type": "text", "text": prompt}]}]}
        headers = {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
    else:
        body = {"model": cfg["model"], "max_tokens": 2000, "temperature": 0,
                "messages": [{"role": "system", "content": VISION_SYS},
                             {"role": "user", "content": [{"type": "text", "text": prompt},
                                                          {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]}
        headers = {"authorization": "Bearer " + cfg["key"], "content-type": "application/json"}
    headers["user-agent"] = "Mozilla/5.0 (X11; Linux x86_64) cryptonova/1.0"
    req = urllib.request.Request(cfg["url"], data=json.dumps(body).encode(), headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            msg = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ParseError("model_busy") from None
    if cfg["style"] == "anthropic":
        txt = "".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
    else:
        txt = (((msg.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    return from_model(parse_json(txt or ""), known)


def llm_text_holdings(llm, text: str, known=frozenset()) -> list[dict]:
    """a statement's text read by the text model (when the line reader found nothing); the text is capped at 12 000 characters"""
    from cnq.agents.llm import parse_json
    try:
        raw = llm.complete(VISION_SYS, "Statement text:\n<<<\n" + text[:12000] + "\n>>>\nJSON only.", temperature=0)
    except Exception:  # noqa: BLE001
        raise ParseError("model_busy") from None
    return from_model(parse_json(raw or "") if isinstance(raw, str) else None, known)

"""Make the FAKE exchange exports for «Разбор портфеля» tests (build 45). No real user data: invented balances, invented ids.
The layouts follow what OKX, BingX and Binance let a user download (balances, bills, transaction history), with the usual noise:
title rows, a «Total» row, zero balances, fiat, an Earn copy (LDxxx), semicolons and decimal commas, several accounts.

    python3 tests/fixtures/portfolio/make_samples.py      (needs openpyxl only to write the .xlsx files)

expected.json = for every file, what the user really holds: {coin: amount}."""
from __future__ import annotations

import json
import os
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
EXP: dict[str, dict[str, float]] = {}


def w(name: str, text: str, enc: str = "utf-8") -> None:
    with open(os.path.join(HERE, name), "w", encoding=enc, newline="") as f:
        f.write(text)


# 1. OKX — trading account bills (CSV): the balance after every line; newest first
okx_bills = [
    ("2026-10-21 18:04:11", "Trade", "BTC-USDT", "Buy", "0.012", "BTC", "0.012", "0.0412"),
    ("2026-10-21 18:04:11", "Trade", "BTC-USDT", "Buy", "-983.8", "USDT", "-983.8", "1250.4"),
    ("2026-10-20 09:12:40", "Trade", "SOL-USDT", "Buy", "4.5", "SOL", "4.5", "16.5"),
    ("2026-10-20 09:12:40", "Trade", "SOL-USDT", "Buy", "-469.6", "USDT", "-469.6", "2234.2"),
    ("2026-10-19 22:30:05", "Transfer in", "", "", "500", "USDT", "500", "2703.8"),
    ("2026-10-18 14:01:59", "Trade", "LINK-USDT", "Buy", "60", "LINK", "60", "60"),
    ("2026-10-18 14:01:59", "Trade", "LINK-USDT", "Buy", "-724.2", "USDT", "-724.2", "2203.8"),
    ("2026-10-17 08:45:00", "Trade", "ETH-USDT", "Sell", "-0.2", "ETH", "-0.2", "1.35"),
    ("2026-10-17 08:45:00", "Trade", "ETH-USDT", "Sell", "481.9", "USDT", "481.9", "2928"),
    ("2026-10-16 11:20:31", "Trade", "BTC-USDT", "Buy", "0.0292", "BTC", "0.0292", "0.0292"),
    ("2026-10-16 11:20:31", "Trade", "BTC-USDT", "Buy", "-2394", "USDT", "-2394", "2446.1"),
    ("2026-10-15 19:33:02", "Trade", "PEPE-USDT", "Buy", "25000000", "PEPE", "25000000", "25000000"),
    ("2026-10-15 19:33:02", "Trade", "PEPE-USDT", "Buy", "-112", "USDT", "-112", "4840.1"),
    ("2026-10-14 07:00:00", "Trade", "SOL-USDT", "Buy", "12", "SOL", "12", "12"),
    ("2026-10-14 07:00:00", "Trade", "DOGE-USDT", "Sell", "-3000", "DOGE", "-3000", "0"),
    ("2026-10-13 10:10:10", "Trade", "ETH-USDT", "Buy", "1.55", "ETH", "1.55", "1.55"),
]
rows = ["id,Order id,Time,Trade Type,Symbol,Action,Amount,Trading Unit,Fill Price,PnL,Fee,Fee Unit,Position Change,"
        "Position Balance,Balance Change,Balance,Balance Unit"]
for n, (t, tt, sym, act, amt, unit, chg, bal) in enumerate(okx_bills):
    rows.append(f"7731{n:05d},{'91022' + str(n) if sym else ''},{t},{tt},{sym},{act},{amt},{unit},,0,0,{unit},0,0,{chg},{bal},{unit}")
w("okx_bills.csv", "\n".join(rows) + "\n")
EXP["okx_bills.csv"] = {"BTC": 0.0412, "USDT": 1250.4, "SOL": 16.5, "LINK": 60, "ETH": 1.35, "PEPE": 25000000}

# 2. BingX — spot assets (CSV, European settings: «;» and decimal commas, a title line, a zero row, a total row)
bingx = [("BTC", "0,02134", "0,02", "0,00134", "1 749,52"), ("ETH", "0,8", "0,8", "0", "1 927,57"),
         ("XRP", "540", "540", "0", "718,20"), ("DOGE", "12 500", "12 500", "0", "990,28"), ("USDT", "312,44", "300", "12,44", "312,44"),
         ("ADA", "1 800", "1 800", "0", "389,82"), ("SUI", "210,5", "200", "10,5", "207,89"), ("HYPE", "3,2", "3,2", "0", "251,01"),
         ("TON", "45", "45", "0", "121,50"), ("AVAX", "0", "0", "0", "0"), ("WIF", "650", "650", "0", "129,48")]
lines = ["BingX · Spot account · assets", "Coin;Total;Available;Frozen;Estimated value (USDT)"]
lines += [";".join(r) for r in bingx]
lines.append("Total;;;;6 797,71")
w("bingx_spot_assets.csv", "\r\n".join(lines) + "\r\n")
EXP["bingx_spot_assets.csv"] = {"BTC": 0.02134, "ETH": 0.8, "XRP": 540, "DOGE": 12500, "USDT": 312.44, "ADA": 1800, "SUI": 210.5,
                                "HYPE": 3.2, "TON": 45, "WIF": 650}

# 3. Binance — transaction history (CSV ledger: the sum of changes per coin over Spot and Earn)
bn = [("2026-09-01 10:00:00", "Spot", "Deposit", "USDT", "5000"),
      ("2026-09-01 10:05:12", "Spot", "Transaction Spend", "USDT", "-2459.49"),
      ("2026-09-01 10:05:12", "Spot", "Transaction Buy", "BTC", "0.03"),
      ("2026-09-01 10:05:12", "Spot", "Transaction Fee", "BTC", "-0.00003"),
      ("2026-09-02 12:00:00", "Spot", "Transaction Spend", "USDT", "-1204.73"),
      ("2026-09-02 12:00:00", "Spot", "Transaction Buy", "ETH", "0.5"),
      ("2026-09-02 12:00:00", "Spot", "Transaction Fee", "BNB", "-0.0012"),
      ("2026-09-03 09:30:00", "Spot", "Deposit", "BNB", "0.5"),
      ("2026-09-05 15:15:15", "Spot", "Simple Earn Flexible Subscription", "USDT", "-1000"),
      ("2026-09-05 15:15:15", "Earn", "Simple Earn Flexible Subscription", "USDT", "1000"),
      ("2026-09-10 00:00:00", "Earn", "Simple Earn Flexible Interest", "USDT", "1.37"),
      ("2026-09-12 18:40:02", "Spot", "Binance Convert", "USDT", "-300"),
      ("2026-09-12 18:40:02", "Spot", "Binance Convert", "LINK", "24.85"),
      ("2026-09-20 08:00:00", "Spot", "Transaction Spend", "USDT", "-200"),
      ("2026-09-20 08:00:00", "Spot", "Transaction Buy", "FET", "1069.9"),
      ("2026-10-01 13:13:13", "Spot", "Withdraw", "ETH", "-0.1"),
      ("2026-10-02 16:20:00", "Spot", "Small assets exchange BNB", "FET", "-0.9"),
      ("2026-10-03 11:00:00", "Spot", "Airdrop Assets", "ENA", "155.2")]
lines = ["User_ID,UTC_Time,Account,Operation,Coin,Change,Remark"]
lines += [f"84720193,{t},{a},{op},{c},{x}," for t, a, op, c, x in bn]
w("binance_transactions.csv", "\n".join(lines) + "\n")
EXP["binance_transactions.csv"] = {"USDT": 837.15, "BTC": 0.02997, "ETH": 0.4, "BNB": 0.4988, "LINK": 24.85, "FET": 1069.0,
                                   "ENA": 155.2}


def xlsx(name: str, sheet_rows: list[list], title: str) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in sheet_rows:
        ws.append(r)
    wb.save(os.path.join(HERE, name))


# 4. OKX — asset overview (XLSX): a title, the export time, two accounts per coin (Funding + Trading), an Earn line
okx_assets = [["OKX — Assets overview"], ["Exported at 2026-10-22 08:14:03 (UTC+3)"], [],
              ["Account", "Ccy", "Equity", "Available", "Frozen", "Equity (USD)"],
              ["Trading", "BTC", 0.1, 0.1, 0, 8198.3], ["Funding", "BTC", 0.025, 0.025, 0, 2049.58],
              ["Trading", "ETH", 2.4, 2.0, 0.4, 5782.7], ["Trading", "SOL", 30, 30, 0, 3130.8],
              ["Funding", "USDC", 1500, 1500, 0, 1500], ["Trading", "ZEC", 1.2, 1.2, 0, 1510.94],
              ["Trading", "AAVE", 5, 5, 0, 740.55], ["Funding", "EUR", 120, 120, 0, 139.2],
              ["Trading", "OP", 4000, 4000, 0, 487.6], ["Trading", "TIA", 0, 0, 0, 0],
              ["Earn", "USDC", 2000, 0, 2000, 2000], ["Trading", "NEAR", 150, 150, 0, 750]]
xlsx("okx_assets.xlsx", okx_assets, "Assets")
EXP["okx_assets.xlsx"] = {"BTC": 0.125, "ETH": 2.4, "SOL": 30, "USDC": 3500, "ZEC": 1.2, "AAVE": 5, "OP": 4000, "NEAR": 150}

# 5. BingX — 30 coins (XLSX): the «30-coin portfolio» of the speed test
coins30 = [("BTC", 0.05), ("ETH", 1.2), ("SOL", 15), ("LINK", 80), ("NEAR", 300), ("AAVE", 3), ("ZEC", 0.8), ("XMR", 1.5),
           ("DOGE", 9000), ("PEPE", 40000000), ("ADA", 2500), ("XRP", 700), ("BNB", 0.6), ("TRX", 2000), ("AVAX", 30),
           ("DOT", 250), ("ARB", 1500), ("SUI", 400), ("LTC", 4), ("UNI", 40), ("ATOM", 90), ("INJ", 25), ("SHIB", 30000000),
           ("PENGU", 25000), ("ENA", 1200), ("JUP", 800), ("TAO", 1.1), ("HYPE", 4), ("ONDO", 900), ("USDT", 1800)]
rows = [["BingX"], ["Asset", "Available", "Frozen", "Total", "USDT Value"]]
for s, a in coins30:
    rows.append([s, a, 0, a, None])
xlsx("bingx_assets_30.xlsx", rows, "Spot")
EXP["bingx_assets_30.xlsx"] = {s: a for s, a in coins30}

# 6. Binance — balances (XLSX): Simple Earn copies as LDxxx, «In Order», a BTC valuation column, a dust line
bn_bal = [["Coin", "Total", "Available", "In Order", "BTC Valuation"],
          ["BTC", "0.0461", "0.0461", "0", "0.0461"], ["LDBTC", "0.01", "0.01", "0", "0.01"],
          ["ETH", "0.92", "0.9", "0.02", "0.02704"], ["LDUSDT", "650.25", "650.25", "0", "0.00793"],
          ["USDT", "120.5", "100.5", "20", "0.00147"], ["BNB", "1.1", "1.1", "0", "0.00986"],
          ["SOL", "8.25", "8.25", "0", "0.0105"], ["SHIB", "0.00000001", "0.00000001", "0", "0"],
          ["LDO", "300", "300", "0", "0.00144"], ["TAO", "0.5", "0.5", "0", "0.00158"], ["XRP", "0", "0", "0", "0"]]
xlsx("binance_balances.xlsx", bn_bal, "Balances")
EXP["binance_balances.xlsx"] = {"BTC": 0.0561, "ETH": 0.92, "USDT": 770.75, "BNB": 1.1, "SOL": 8.25, "SHIB": 0.00000001,
                                "LDO": 300, "TAO": 0.5}


# 7. A PDF statement (text, Helvetica, the page content compressed): «Coin · Total · Available · Frozen · Value (USD)»
def pdf(name: str, lines: list[list[str]]) -> None:
    ops = ["BT", "/F1 10 Tf"]
    y = 780
    for cells in lines:
        x0 = 50
        for i, c in enumerate(cells):
            ops.append(f"1 0 0 1 {x0 + i * 100} {y} Tm ({c}) Tj")
        y -= 18
    ops.append("ET")
    content = zlib.compress("\n".join(ops).encode("latin-1"))
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        if o is None:
            out += f"{i} 0 obj\n<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode() + content + b"\nendstream\nendobj\n"
        else:
            out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    with open(os.path.join(HERE, name), "wb") as f:
        f.write(out)


pdf("statement.pdf", [["Account statement", "", "", "", ""], ["Period: 01.10.2026 - 21.10.2026"], [],
                      ["Coin", "Total", "Available", "Frozen", "Value (USD)"],
                      ["BTC", "0.0835", "0.08", "0.0035", "6,845.58"], ["ETH", "1.75", "1.75", "0", "4,216.56"],
                      ["USDT", "2,310.00", "2,000.00", "310.00", "2,310.00"], ["LINK", "45", "45", "0", "543.15"],
                      ["ARB", "2,200", "2,200", "0", "397.35"], ["PENGU", "60,000", "60,000", "0", "471.13"],
                      ["Total value", "", "", "", "14,783.77"]])
EXP["statement.pdf"] = {"BTC": 0.0835, "ETH": 1.75, "USDT": 2310, "LINK": 45, "ARB": 2200, "PENGU": 60000}

with open(os.path.join(HERE, "expected.json"), "w", encoding="utf-8") as f:
    json.dump(EXP, f, indent=1, ensure_ascii=False)
print("samples:", ", ".join(EXP))

"""Loader for the open FX/metals dataset (github.com/ejtraderLabs/historical-data):
12 FX pairs + XAUUSD, m15/m30/h1/h4/d1, 2012–2022, broker time (EET), prices scaled."""
from __future__ import annotations

import csv
from datetime import datetime, timezone

from ..bingx.rest import Candle

SCALE = {"XAUUSD": 100.0}
JPY = ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY")
# per-side cost as fraction of price (spread/2 + commission), typical ECN retail
COST = {"EURUSD": 0.00004, "GBPUSD": 0.00005, "AUDUSD": 0.00005, "USDCAD": 0.00006, "USDCHF": 0.00006,
        "EURGBP": 0.00006, "EURCHF": 0.00007, "USDJPY": 0.00005, "EURJPY": 0.00007, "GBPJPY": 0.00009, "AUDJPY": 0.00008,
        "XAUUSD": 0.00012}
TF_MS = {"m15": 900_000, "m30": 1_800_000, "h1": 3_600_000, "h4": 14_400_000, "d1": 86_400_000}


def scale(sym: str) -> float:
    return SCALE.get(sym, 1000.0 if sym in JPY else 100000.0)


def load(root: str, sym: str, tf: str = "h1") -> list[Candle]:
    k = scale(sym); ms = TF_MS[tf]
    out = []
    with open(f"{root}/{sym}/{sym}{tf}.csv", newline="") as f:
        for r in csv.DictReader(f):
            d = r["Date"]
            dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S" if len(d) > 10 else "%Y-%m-%d").replace(tzinfo=timezone.utc)
            t = int(dt.timestamp() * 1000)
            o, h, l, c = (float(r[x]) / k for x in ("open", "high", "low", "close"))
            v = float(r["tick_volume"])
            out.append(Candle(t, o, h, l, c, v, t + ms - 1, v * c, int(v), v / 2, v / 2 * c))
    out.sort(key=lambda x: x.open_time)
    return out

"""Synthetic candle generator for tests: a random walk that respects a few
horizontal levels (so the level engine has something to find). Not for research —
only to exercise code paths deterministically."""
from __future__ import annotations

import random

from cnq.bingx.rest import Candle


def make_candles(n: int = 3000, start_price: float = 60_000.0, interval_ms: int = 900_000,
                 seed: int = 7, levels: tuple[float, ...] = (58_000, 60_000, 62_000)) -> list[Candle]:
    rng = random.Random(seed)
    t0 = 1_700_000_000_000
    px = start_price
    out = []
    for i in range(n):
        drift = rng.gauss(0, 120)
        # price gets pushed back when it crosses a level (bounce behaviour)
        for L in levels:
            if abs(px - L) < 150 and rng.random() < 0.6:
                drift += (L - px) * 0.5 + rng.choice((-1, 1)) * 60
        o = px
        c = max(1000.0, px + drift)
        h = max(o, c) + abs(rng.gauss(0, 60))
        lo = min(o, c) - abs(rng.gauss(0, 60))
        vol = abs(rng.gauss(50, 15)) * (1.8 if abs(drift) > 200 else 1.0)
        tb = vol * (0.5 + (0.2 if c > o else -0.2) * rng.random())
        out.append(Candle(t0 + i * interval_ms, o, h, lo, c, vol, t0 + (i + 1) * interval_ms - 1,
                          vol * c, int(vol * 10), tb, tb * c))
        px = c
    return out

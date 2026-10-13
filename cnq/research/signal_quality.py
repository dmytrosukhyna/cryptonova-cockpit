"""Signal quality independent of the TP ladder.

For an entry at candle i+1 open with stop distance R, we ask: within `horizon`
candles, does price reach +kR before −1R?  P(k) for k in 0.5,1,2,3 is the
"edge curve" of the entry rule. A random entry on the same instrument with the
same stop sizes gives the baseline curve; the rule has edge only if it beats it
consistently out-of-sample. No fees here — this measures the signal, not the
costs."""
from __future__ import annotations

import random
from collections import defaultdict

import numpy as np

from ..bingx.rest import Candle

K_LEVELS = (0.5, 1.0, 1.5, 2.0, 3.0)


def first_hit(highs: np.ndarray, lows: np.ndarray, entry: float, stop: float, sign: int,
              start: int, horizon: int) -> dict[float, bool]:
    """For each k: True if +kR was reached before −1R within the horizon (stop-first on ties)."""
    r = abs(entry - stop)
    out: dict[float, bool] = {}
    end = min(start + horizon, len(highs))
    stop_idx = None
    for j in range(start, end):
        hit_stop = lows[j] <= stop if sign == 1 else highs[j] >= stop
        if hit_stop:
            stop_idx = j
            break
    for k in K_LEVELS:
        tgt = entry + sign * k * r
        reached = None
        for j in range(start, end):
            if (highs[j] >= tgt) if sign == 1 else (lows[j] <= tgt):
                reached = j
                break
        # conservative: same candle as stop counts as stop
        out[k] = reached is not None and (stop_idx is None or reached < stop_idx)
    return out


def edge_curve(candles: list[Candle], entries: list[tuple[int, str, float]], horizon: int = 96) -> dict:
    """entries: (call_index, direction, stop_price). Entry = open of call_index+1."""
    highs = np.array([c.high for c in candles]); lows = np.array([c.low for c in candles])
    opens = np.array([c.open for c in candles])
    hits = defaultdict(list)
    for ci, direction, stop in entries:
        if ci + 1 >= len(candles):
            continue
        sign = 1 if direction == "LONG" else -1
        entry = opens[ci + 1]
        if (sign == 1 and stop >= entry) or (sign == -1 and stop <= entry):
            continue
        res = first_hit(highs, lows, entry, stop, sign, ci + 1, horizon)
        for k, v in res.items():
            hits[k].append(v)
    return {k: round(float(np.mean(v)), 3) for k, v in sorted(hits.items())} | {"n": len(hits[K_LEVELS[0]])}


def random_baseline(candles: list[Candle], entries: list[tuple[int, str, float]], horizon: int = 96,
                    seed: int = 1, reps: int = 3) -> dict:
    """Same number of entries, same direction mix, same stop distances (in price),
    random times. Averaged over `reps` draws."""
    rng = random.Random(seed)
    opens = [c.open for c in candles]
    acc = defaultdict(list)
    for _ in range(reps):
        fake = []
        for ci, direction, stop in entries:
            if ci + 1 >= len(candles):
                continue
            dist = abs(opens[ci + 1] - stop)
            j = rng.randrange(30, len(candles) - horizon - 2)
            sign = 1 if direction == "LONG" else -1
            fake.append((j, direction, opens[j + 1] - sign * dist))
        for k, v in edge_curve(candles, fake, horizon).items():
            acc[k].append(v)
    return {k: round(float(np.mean(v)), 3) for k, v in acc.items()}

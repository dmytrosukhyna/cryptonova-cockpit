"""Thin an equity history for the site and the app (20.10.2026).

Since the server engine runs every minute, every desk writes one equity point a minute, and «the last 300–500 points» covered
only 5–8 hours: the charts were flat lines, «за сегодня» and the worst dip were computed on a few hours. thin() keeps the shape
of the whole account life in about the same number of points: every point of the last 2 hours, one point an hour (the last of
that hour) for the last 10 days, one a day before that, always the very first and the very last point."""
from __future__ import annotations

HOUR = 3_600_000
DAY = 86_400_000


def thin(eqh: list[dict], now: int | None = None, recent_h: float = 2, hourly_days: float = 10, cap: int = 420) -> list[dict]:
    if not eqh:
        return []
    now = now or int(eqh[-1]["time"])
    recent_from = now - recent_h * HOUR
    hourly_from = now - hourly_days * DAY
    out: list[dict] = []
    last_bucket = None
    for e in eqh:
        t = int(e["time"])
        if t >= recent_from:
            out.append(e)
            last_bucket = None
            continue
        b = ("h", t // HOUR) if t >= hourly_from else ("d", t // DAY)
        if b == last_bucket:
            out[-1] = e                  # the last point of each hour / day
        else:
            out.append(e)
            last_bucket = b
    if out[0] is not eqh[0]:
        out.insert(0, eqh[0])            # the start of the account stays on the chart
    if len(out) > cap:
        out = [out[0]] + out[-(cap - 1):]
    return out

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import blend_study as B  # noqa: E402


def _t(y, m, d=5):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000)


def test_monthly_fills_a_month_without_points():
    r = B.monthly([(_t(2024, 5), 1.0), (_t(2024, 5, 20), 1.1), (_t(2024, 7), 1.21)])
    assert list(r) == ["2024-05", "2024-06", "2024-07"]
    assert abs(r["2024-05"] - 0.10) < 1e-9 and r["2024-06"] == 0.0 and abs(r["2024-07"] - 0.10) < 1e-9


def test_stats_drawdown_on_month_ends():
    import numpy as np
    s = B.stats(np.array([0.10, -0.20, 0.05]))
    assert s["dd_me"] == -20.0 and s["wm"] == -20.0

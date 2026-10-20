from cnq.agents.history import DAY, HOUR, thin


def test_thin_keeps_shape_and_recent_detail():
    now = 30 * DAY
    eqh = [{"time": t, "equity": 10000 + t / DAY} for t in range(0, now + 1, 60_000)]      # one point a minute for 30 days
    out = thin(eqh, now)
    assert out[0] is eqh[0] and out[-1] is eqh[-1]
    assert len(out) <= 420
    times = [e["time"] for e in out]
    assert times == sorted(times)
    assert sum(1 for t in times if t >= now - 2 * HOUR) >= 120              # the last 2 hours minute by minute
    hourly = [t for t in times if now - 10 * DAY <= t < now - 2 * HOUR]
    assert 200 <= len(hourly) <= 240                                       # one an hour for 10 days
    older = [t for t in times if t < now - 10 * DAY]
    assert 15 <= len(older) <= 22                                          # one a day before


def test_thin_short_history_unchanged():
    eqh = [{"time": i * 60_000, "equity": 1.0} for i in range(50)]
    assert thin(eqh) == eqh
    assert thin([]) == []

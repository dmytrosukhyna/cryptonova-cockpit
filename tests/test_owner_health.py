"""09.11 (owner 21:40: «я сам ничего не понимаю, привести в порядок все сервисы»): «Состояние системы» — one plain row per service,
🟢 / 🟡 / 🔴, a transient GitHub failure is yellow «временный сбой GitHub, повторится сам», red rows say what to do."""
import datetime as dt
import json
import os
import sqlite3
import tempfile

from cnq.agents import owner_health as H

NOW = int(dt.datetime(2026, 11, 9, 21, 40, tzinfo=H.KYIV).timestamp() * 1000)
M = 60_000


def _web(site_runs):
    d = tempfile.mkdtemp()
    w = lambda n, o: json.dump(o, open(os.path.join(d, n), "w"))
    w("system_status.json", {"components": [{"key": "autopilot", "status": "ok", "last_ok": NOW - M},
                                            {"key": "public_real", "status": "ok", "last_ok": NOW - 10 * M},
                                            {"key": "researcher", "status": "ok", "last_ok": NOW - 30 * M},
                                            {"key": "api", "status": "error", "last_ok": NOW - 300 * M}]})
    w("public_real.json", {"asof": NOW - 10 * M}); w("whales_live.json", {"asof": NOW - 3 * M}); w("status.json", {"time": NOW - 20 * M})
    w("morning.json", {"day": "2026-11-09", "asof": NOW - 900 * M, "audio": "morning.mp3"})
    w("journal.json", {"entries": [{"date": "2026-11-09", "md": "x"}]}); w("app_home.json", {"asof": NOW - 2 * M}); w("engine.json", {"errors": []})
    w("gh_runs.json", {"workflows": {"site": site_runs, "publish": [{"status": "completed", "conclusion": "success", "t_end": NOW - 15 * M}]},
                       "pages_t": NOW - 15 * M})
    return d


def _conn(slots):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE channel_posts(day TEXT, slot TEXT, sent_at INTEGER, text TEXT, PRIMARY KEY(day, slot))")
    for s in slots:
        c.execute("INSERT INTO channel_posts VALUES('2026-11-09',?,?,'')", (s, NOW - 60 * M))
    return c


def test_rows_plain_statuses_and_what_to_do():
    ok = [{"status": "completed", "conclusion": "success", "t_end": NOW - 20 * M}]
    d = H.build(_web(ok), _conn(["morning", "evening"]), NOW)
    rows = {r["key"]: r for r in d["rows"]}
    assert list(rows) == ["autopilot", "traders", "pipeline", "publish", "channel", "morning", "researcher", "journal", "feeds", "nova", "peter"]
    assert all(r["what"] and r["icon"] in "🟢🟡🔴" and r["word"] in ("работает", "задержка", "сбой") for r in rows.values())
    assert rows["autopilot"]["status"] == "ok" and rows["pipeline"]["status"] == "ok" and rows["channel"]["status"] == "ok"
    assert rows["nova"]["status"] == "fail" and "Claude" in rows["nova"]["todo"] and d["overall"] == "fail"
    assert all(r["todo"] is None for r in rows.values() if r["status"] != "fail")
    # a transient GitHub failure after a success → yellow, repeats by itself
    tr = [{"status": "completed", "conclusion": "failure", "failed_step": "Run actions/checkout@v4", "t_end": NOW - 5 * M}] + ok
    p = {r["key"]: r for r in H.build(_web(tr), _conn(["morning"]), NOW)["rows"]}["pipeline"]
    assert p["status"] == "delay" and "временный сбой GitHub, повторится сам" in p["note"]
    # two failures in a row → red; no evening post after 22:15 → red
    two = [dict(tr[0], failed_step="News desk"), dict(tr[0], failed_step="News desk")]
    late = NOW + 50 * M
    rows2 = {r["key"]: r for r in H.build(_web(two), _conn(["morning"]), late)["rows"]}
    assert rows2["pipeline"]["status"] == "fail" and "два запуска подряд" in rows2["pipeline"]["note"]
    assert rows2["channel"]["status"] == "fail" and "вечерний" in rows2["channel"]["note"]


def test_yellow_rows_say_why_and_ci_reads_the_newest_push(monkeypatch):
    """10.11: «Ленты приложения» was yellow with an empty note — the pipeline read its own 20-minute-old checkout"""
    ok = [{"status": "completed", "conclusion": "success", "t_end": NOW - 20 * M}]
    d = _web(ok)
    json.dump({"asof": NOW - 22 * M}, open(os.path.join(d, "app_home.json"), "w"))
    row = {r["key"]: r for r in H.build(d, _conn(["morning"]), NOW)["rows"]}["feeds"]
    assert row["status"] == "delay" and row["note"] == "последнее обновление 22 мин назад"
    # in the pipeline the server's newest push (origin/main) is read, not the checkout
    import subprocess

    class R:
        def __init__(self, rc, out=b""):
            self.returncode, self.stdout = rc, out
    calls = []

    def fake(cmd, **k):
        calls.append(cmd)
        return R(0, json.dumps({"asof": NOW - M}).encode()) if cmd[:2] == ["git", "show"] else R(0)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(subprocess, "run", fake)
    H._LIVE.clear()
    row = {r["key"]: r for r in H.build(d, _conn(["morning"]), NOW)["rows"]}["feeds"]
    H._LIVE.clear()
    assert row["status"] == "ok" and ["git", "show", "FETCH_HEAD:web/app_home.json"] in calls

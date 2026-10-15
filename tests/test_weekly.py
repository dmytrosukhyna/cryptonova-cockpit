import datetime as dt, json, os, sqlite3, tempfile
from cnq.agents import weekly


def test_weekly_review_builds_and_gates():
    rep = json.load(open("web/terminal.json")); conn = sqlite3.connect("data/agents.sqlite")
    p = os.path.join(tempfile.mkdtemp(), "weekly.json")
    assert weekly.should_run(p) == "first"
    doc = weekly.run(conn, rep, p, now_ms=rep["generated"])
    assert "BTC" in doc["text"] and doc["next_week"] and doc["post"].startswith("📊")
    assert weekly.should_run(p, now_ms=rep["generated"] + 3600000) is None
    fri = int(dt.datetime(2026, 10, 16, 17, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert weekly.should_run(p, now_ms=fri) == "friday"
    doc2 = weekly.run(conn, rep, p, now_ms=rep["generated"] + 7 * 86400000)
    assert doc2["said_last_time"] and doc2["said_last_time"]["checks"] and len(doc2["history"]) == 1

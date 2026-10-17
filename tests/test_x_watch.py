"""X watch: nothing without a token; with a token new posts become news items and since_id advances."""
import sqlite3

from cnq.agents import x_watch


def test_no_token_no_calls():
    calls = []
    assert x_watch.fetch(sqlite3.connect(":memory:"), token=None, get=lambda u, t: calls.append(u)) == [] and not calls


def test_posts_become_news_and_since_advances():
    conn = sqlite3.connect(":memory:")
    seen = []

    def get(url, token):
        seen.append(url)
        if "/users/by" in url:
            return {"data": [{"username": "elonmusk", "id": "44196397"}]}
        return {"data": [{"id": "105", "text": "Dogecoin to the moon", "created_at": "2026-10-17T12:00:00.000Z"},
                         {"id": "101", "text": "hello", "created_at": "2026-10-17T11:00:00.000Z"}]}
    items = x_watch.fetch(conn, token="t", accounts=["elonmusk"], get=get)
    assert len(items) == 2 and items[0]["source"] == "x:elonmusk" and "x.com/elonmusk/status/105" in items[0]["link"]
    x_watch.fetch(conn, token="t", accounts=["elonmusk"], get=get)
    assert "since_id=105" in seen[-1]

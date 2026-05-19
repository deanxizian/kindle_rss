from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rss_to_kindle.models import Article
from rss_to_kindle.state import StateStore


def test_sqlite_tables_initialize(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    StateStore(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert {"feeds_state", "articles", "delivery_logs"}.issubset(tables)


def test_sent_article_deduplication(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.db")
    article = Article(
        article_id="a1",
        feed_id="demo",
        feed_name="Demo",
        category="Tech",
        feed_priority=50,
        title="Title",
        url="https://example.com/a",
        guid="guid-1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        summary="summary",
        content_html="<p>summary</p>",
    )

    state.upsert_article(article)
    assert state.is_article_sent("a1") is False

    state.mark_articles_sent(["a1"])
    assert state.is_article_sent("a1") is True


def test_feed_conditional_headers(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.db")
    state.update_feed_success("demo", etag='"abc"', last_modified="Tue, 19 May 2026 00:00:00 GMT")

    assert state.get_conditional_headers("demo") == {
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Tue, 19 May 2026 00:00:00 GMT",
    }

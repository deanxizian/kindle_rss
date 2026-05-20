from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx

from rss_to_kindle.config import AppConfig, FeedConfig
from rss_to_kindle.feed_fetcher import FeedFetcher, collect_digest_articles
from rss_to_kindle.models import Article, FeedFetchResult
from rss_to_kindle.state import StateStore
from rss_to_kindle.utils import article_matches_keywords, compute_article_id, normalize_url


ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Demo Feed</title>
  <entry>
    <title>Python AI News</title>
    <id>tag:example.com,2026:1</id>
    <link href="https://example.com/a?utm_source=x&keep=1#frag" />
    <updated>2026-05-19T00:00:00Z</updated>
    <summary>LLM and Python summary</summary>
  </entry>
  <entry>
    <title>Other News</title>
    <id>tag:example.com,2026:2</id>
    <link href="https://example.com/b" />
    <updated>2026-05-18T00:00:00Z</updated>
    <summary>Other summary</summary>
  </entry>
</feed>
"""


def test_normalize_url_removes_tracking_params() -> None:
    assert (
        normalize_url("https://example.com/a?utm_source=x&b=2&fbclid=abc&utm_campaign=y#section")
        == "https://example.com/a?b=2"
    )


def test_article_id_priority() -> None:
    first = compute_article_id("feed", "guid", "https://example.com/a", "title", None)
    second = compute_article_id("feed", "guid", "https://example.com/b", "other", None)
    assert first == second

    url_a = compute_article_id("feed", None, "https://example.com/a?utm_medium=x#top", "title", None)
    url_b = compute_article_id("feed", None, "https://example.com/a", "title", None)
    assert url_a == url_b


def test_feed_fetcher_parses_atom_and_updates_state(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"etag": '"demo"'},
            content=ATOM.encode("utf-8"),
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = FeedFetcher(client=client)
    state = StateStore(tmp_path / "state.db")
    feed = FeedConfig(
        id="demo",
        name="Demo",
        url="https://example.com/feed.xml",
        category="Tech",
        status="active",
    )

    result = fetcher.fetch_feed(feed, state=state)

    assert result.status_code == 200
    assert result.parsed_title == "Demo Feed"
    assert len(result.articles) == 2
    assert result.articles[0].title == "Python AI News"
    assert state.get_conditional_headers("demo")["If-None-Match"] == '"demo"'


def test_keyword_include_exclude_filtering() -> None:
    article = Article(
        article_id="a1",
        feed_id="demo",
        feed_name="Demo",
        category="Tech",
        feed_priority=50,
        title="Python AI News",
        url=None,
        guid=None,
        published_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        summary="LLM update",
        content_html="<p>Python tooling</p>",
    )

    assert article_matches_keywords(article.title, article.summary, article.content_html, ["python"], [])
    assert not article_matches_keywords(article.title, article.summary, article.content_html, ["ruby"], [])
    assert not article_matches_keywords(article.title, article.summary, article.content_html, [], ["llm"])


def test_collect_digest_articles_filters_past_lookback_window(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "version": 1,
            "digest": {
                "title": "Daily",
                "language": "zh-CN",
                "timezone": "Asia/Shanghai",
                "default_oldest_hours": 24,
                "default_max_items_per_feed": 10,
                "max_total_articles": 80,
                "max_epub_mb": 45,
                "include_feed_health_page": True,
            },
            "delivery": {
                "kindle_email": "kindle@example.com",
                "sender_email": "sender@example.com",
            },
            "categories": ["Tech"],
            "feeds": [
                {
                    "id": "demo",
                    "name": "Demo",
                    "url": "https://example.com/feed.xml",
                    "category": "Tech",
                    "status": "active",
                    "full_text": False,
                }
            ],
        }
    )
    articles = [
        _article("recent", "Recent", datetime(2026, 5, 18, 22, 30, tzinfo=timezone.utc)),
        _article("old", "Too Old", datetime(2026, 5, 18, 21, 59, tzinfo=timezone.utc)),
        _article("future", "Future", datetime(2026, 5, 19, 22, 1, tzinfo=timezone.utc)),
        _article("unknown", "Unknown", None),
    ]

    collected = collect_digest_articles(
        config,
        state=StateStore(tmp_path / "state.db"),
        window_end=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        fetcher=_DummyFetcher(articles),
        extractor=object(),
    )

    assert [article.title for article in collected] == ["Recent"]


def test_collect_digest_articles_persist_false_does_not_upsert_articles(tmp_path: Path) -> None:
    config = _config()
    state = StateStore(tmp_path / "state.db")

    collected = collect_digest_articles(
        config,
        state=state,
        window_end=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        fetcher=_DummyFetcher([_article("recent", "Recent", datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc))]),
        extractor=object(),
        persist=False,
    )

    with sqlite3.connect(state.path) as connection:
        article_count = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    assert [article.article_id for article in collected] == ["recent"]
    assert article_count == 0


class _DummyFetcher:
    def __init__(self, articles: list[Article]) -> None:
        self.articles = articles

    def fetch_feed(self, feed: FeedConfig, state: StateStore | None = None, update_health: bool = True) -> FeedFetchResult:
        return FeedFetchResult(feed_id=feed.id, feed_name=feed.name, status_code=200, parsed_title=feed.name, articles=self.articles)


def _article(article_id: str, title: str, published_at: datetime | None) -> Article:
    return Article(
        article_id=article_id,
        feed_id="demo",
        feed_name="Demo",
        category="Tech",
        feed_priority=50,
        title=title,
        url="https://example.com/article",
        guid=article_id,
        published_at=published_at,
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        summary="summary",
        content_html="<p>summary</p>",
    )


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "version": 1,
            "digest": {
                "title": "Daily",
                "language": "zh-CN",
                "timezone": "Asia/Shanghai",
                "default_oldest_hours": 24,
                "default_max_items_per_feed": 10,
                "max_total_articles": 80,
                "max_epub_mb": 45,
                "include_feed_health_page": True,
            },
            "delivery": {
                "kindle_email": "kindle@example.com",
                "sender_email": "sender@example.com",
            },
            "categories": ["Tech"],
            "feeds": [
                {
                    "id": "demo",
                    "name": "Demo",
                    "url": "https://example.com/feed.xml",
                    "category": "Tech",
                    "status": "active",
                    "full_text": False,
                }
            ],
        }
    )

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from rss_to_kindle.config import FeedConfig
from rss_to_kindle.feed_fetcher import FeedFetcher
from rss_to_kindle.models import Article
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

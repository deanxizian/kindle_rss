from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from ebooklib import epub

from rss_to_kindle.config import load_config
from rss_to_kindle.epub_builder import build_epub
from rss_to_kindle.models import Article


def test_epub_builder_generates_non_empty_epub(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "feeds.example.yml")
    article = Article(
        article_id="a1",
        feed_id="demo",
        feed_name="Demo Feed",
        category="Tech",
        feed_priority=80,
        title="Python News",
        url="https://example.com/a",
        guid="guid-1",
        published_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        summary="Summary",
        content_html="<p>Hello <strong>Kindle</strong>.</p>",
    )

    output = build_epub([article], config, output=tmp_path, digest_date=date(2026, 5, 19))

    assert output.exists()
    assert output.stat().st_size > 0
    book = epub.read_epub(str(output))
    assert book.get_metadata("DC", "title")[0][0] == "每日 RSS 摘要 - 2026-05-19"

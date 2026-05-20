from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import rss_to_kindle.cli as cli_module
from rss_to_kindle.epub_builder import BuildResult
from rss_to_kindle.models import Article
from rss_to_kindle.utils import read_manifest


def test_build_manifest_uses_actual_build_result_article_ids(tmp_path: Path, monkeypatch) -> None:
    config_path = Path(__file__).parents[1] / "feeds.example.yml"
    epub_path = tmp_path / "daily-rss-2026-05-19.epub"
    original_articles = [_article("kept"), _article("dropped")]

    monkeypatch.setenv("RSS2KINDLE_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setattr(cli_module, "collect_digest_articles", lambda *args, **kwargs: original_articles)

    def fake_build_epub(*args, **kwargs) -> BuildResult:
        epub_path.write_bytes(b"epub")
        return BuildResult(epub_path=epub_path, article_ids=["kept"], article_count=1)

    monkeypatch.setattr(cli_module, "build_epub", fake_build_epub)

    cli_module.build(
        config=config_path,
        output=tmp_path,
        include_testing=False,
        dry_run=False,
        date_value="2026-05-19",
        limit=None,
    )

    manifest = read_manifest(epub_path)

    assert manifest is not None
    assert manifest["article_ids"] == ["kept"]
    assert manifest["article_count"] == 1


def _article(article_id: str) -> Article:
    return Article(
        article_id=article_id,
        feed_id="demo",
        feed_name="Demo Feed",
        category="Tech",
        feed_priority=80,
        title=f"Article {article_id}",
        url=f"https://example.com/{article_id}",
        guid=article_id,
        published_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        summary="Summary",
        content_html="<p>Summary</p>",
    )

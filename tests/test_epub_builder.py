from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import httpx
from ebooklib import epub

import rss_to_kindle.epub_builder as epub_builder
from rss_to_kindle.config import load_config
from rss_to_kindle.epub_builder import build_epub, _download_image
from rss_to_kindle.models import Article


def test_epub_builder_generates_non_empty_epub(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "feeds.example.yml")
    article = _article(
        article_id="a1",
        title="Python News",
        guid="guid-1",
        content_html=(
            '<p>Hello <strong>Kindle</strong>.</p>'
            '<p><img src="https://example.com/image-1.png" alt="demo 1"/></p>'
            '<p><img src="https://example.com/image-2.png" alt="demo 2"/></p>'
            '<p><img src="https://example.com/image-3.png" alt="demo 3"/></p>'
        ),
    )
    image_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=_TINY_PNG,
                request=request,
            )
        )
    )

    result = build_epub([article], config, output=tmp_path, digest_date=date(2026, 5, 19), image_client=image_client)
    output = result.epub_path

    assert output.exists()
    assert output.stat().st_size > 0
    assert result.article_ids == ["a1"]
    assert result.article_count == 1
    assert list(tmp_path.glob("*.epub")) == [output]
    with ZipFile(output) as archive:
        cover = archive.read("EPUB/cover.xhtml")
        directory = archive.read("EPUB/directory.xhtml")
        article_files = [name for name in archive.namelist() if name.startswith("EPUB/articles/")]
        image_files = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        first_article = archive.read(article_files[0])
    assert b"Python News" in first_article
    assert b"Hello" in first_article
    assert b"../images/" in first_article
    assert b'href="../style/main.css"' in first_article
    assert b'href="style/main.css"' in cover
    assert b'href="style/main.css"' in directory
    assert len(image_files) == 3
    assert archive_name_suffix(image_files[0]) == ".png"
    assert len(cover) > 0
    assert "文章数量".encode("utf-8") in cover
    assert len(directory) > 0
    book = epub.read_epub(str(output))
    assert book.get_metadata("DC", "title")[0][0] == "每日 RSS 摘要 - 2026-05-19"


def test_epub_builder_result_tracks_articles_after_size_reduction(tmp_path: Path, monkeypatch) -> None:
    config = load_config(Path(__file__).parents[1] / "feeds.example.yml")
    config = config.model_copy(update={"digest": config.digest.model_copy(update={"max_epub_mb": 1})})
    short_article = _article("short", "Short", "short", "<p>short</p>")
    long_article = _article("long", "Long", "long", "<p>" + ("long " * 200) + "</p>")

    def fake_write_epub(articles, config, output_path, *args, **kwargs) -> None:
        if len(articles) > 1:
            output_path.write_bytes(b"x" * (1024 * 1024 + 1))
            return
        output_path.write_bytes(b"x")

    monkeypatch.setattr(epub_builder, "_write_epub", fake_write_epub)

    result = build_epub(
        [short_article, long_article],
        config,
        output=tmp_path,
        digest_date=date(2026, 5, 19),
    )

    assert result.article_ids == ["short"]
    assert result.article_count == 1


def test_download_image_skips_images_larger_than_3mb() -> None:
    image_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=b"x" * (3 * 1024 * 1024 + 1),
                request=request,
            )
        )
    )

    assert _download_image("https://example.com/large.jpg", image_client, max_image_bytes=3 * 1024 * 1024) is None


def archive_name_suffix(name: str) -> str:
    return Path(name).suffix


def _article(article_id: str, title: str, guid: str, content_html: str) -> Article:
    return Article(
        article_id=article_id,
        feed_id="demo",
        feed_name="Demo Feed",
        category="Tech",
        feed_priority=80,
        title=title,
        url="https://example.com/a",
        guid=guid,
        published_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        summary="Summary",
        content_html=content_html,
    )


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

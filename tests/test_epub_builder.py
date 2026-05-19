from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import httpx
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

    output = build_epub([article], config, output=tmp_path, digest_date=date(2026, 5, 19), image_client=image_client)

    assert output.exists()
    assert output.stat().st_size > 0
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
    assert len(image_files) == 3
    assert archive_name_suffix(image_files[0]) == ".png"
    assert len(cover) > 0
    assert "文章数量".encode("utf-8") in cover
    assert len(directory) > 0
    book = epub.read_epub(str(output))
    assert book.get_metadata("DC", "title")[0][0] == "每日 RSS 摘要 - 2026-05-19"


def archive_name_suffix(name: str) -> str:
    return Path(name).suffix


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

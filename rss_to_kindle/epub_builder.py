from __future__ import annotations

import hashlib
import logging
import mimetypes
import posixpath
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from ebooklib import epub

from .article_extractor import clean_html
from .config import AppConfig
from .models import Article
from .utils import escape_attr, format_datetime, html_to_text, sanitize_filename

logger = logging.getLogger(__name__)


class EpubSizeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BuildResult:
    epub_path: Path
    article_ids: list[str]
    article_count: int


def build_epub(
    articles: list[Article],
    config: AppConfig,
    output: str | Path = "output",
    digest_date: date | None = None,
    feed_health: list[dict[str, object]] | None = None,
    image_client: httpx.Client | None = None,
) -> BuildResult:
    digest_date = digest_date or date.today()
    output_path = _resolve_output_path(Path(output), digest_date)
    max_bytes = config.digest.max_epub_mb * 1024 * 1024
    current_articles = list(articles)
    strip_images = False

    while True:
        _write_epub(
            current_articles,
            config,
            output_path,
            digest_date,
            feed_health or [],
            strip_images=strip_images,
            image_client=image_client,
            image_budget_bytes=_image_budget_bytes(max_bytes, config.digest.max_image_budget_mb),
            max_images_per_article=config.digest.max_images_per_article,
        )
        size = output_path.stat().st_size
        logger.info("Generated EPUB: %s (%s bytes)", output_path, size)
        if size <= max_bytes:
            article_ids = [article.article_id for article in current_articles]
            return BuildResult(epub_path=output_path, article_ids=article_ids, article_count=len(article_ids))

        if not strip_images:
            logger.warning("EPUB is larger than %s MB; rebuilding without images", config.digest.max_epub_mb)
            strip_images = True
            continue

        if current_articles:
            longest = max(current_articles, key=lambda article: len(html_to_text(article.content_html)))
            logger.warning("EPUB is still too large; dropping long article: %s", longest.title)
            current_articles.remove(longest)
            continue

        raise EpubSizeError(
            f"EPUB exceeds configured max_epub_mb={config.digest.max_epub_mb} even after removing images/articles"
        )


def _resolve_output_path(output: Path, digest_date: date) -> Path:
    if output.suffix.lower() == ".epub":
        output.parent.mkdir(parents=True, exist_ok=True)
        return output
    output.mkdir(parents=True, exist_ok=True)
    return output / f"daily-rss-{digest_date.isoformat()}.epub"


def _write_epub(
    articles: list[Article],
    config: AppConfig,
    output_path: Path,
    digest_date: date,
    feed_health: list[dict[str, object]],
    strip_images: bool,
    image_client: httpx.Client | None,
    image_budget_bytes: int,
    max_images_per_article: int,
) -> None:
    title = f"{config.digest.title} - {digest_date.isoformat()}"
    book = epub.EpubBook()
    book.set_identifier(f"rss-to-kindle-{digest_date.isoformat()}")
    book.set_title(title)
    book.set_language(config.digest.language)
    book.add_author("rss-to-kindle")

    css = epub.EpubItem(
        uid="main-css",
        file_name="style/main.css",
        media_type="text/css",
        content=_css().encode("utf-8"),
    )
    book.add_item(css)

    owns_image_client = image_client is None
    image_client = image_client or httpx.Client(
        follow_redirects=True,
        timeout=20.0,
        headers={"User-Agent": "rss-to-kindle/0.1"},
    )
    image_cache: dict[str, str] = {}
    image_budget_state = {"used": 0, "limit": image_budget_bytes}

    cover_page = _make_page(
        title="封面",
        file_name="cover.xhtml",
        body=f"""
        <section class="cover">
          <h1>{escape_attr(title)}</h1>
          <p>生成日期：{escape_attr(digest_date.isoformat())}</p>
          <p>文章数量：{len(articles)}</p>
        </section>
        """,
        language=config.digest.language,
        css=css,
    )
    book.add_item(cover_page)

    article_pages: list[epub.EpubHtml] = []
    used_file_names: set[str] = set()
    try:
        for index, article in enumerate(articles, start=1):
            file_name = _article_file_name(index, article, used_file_names)
            body = _article_body(
                article,
                config.digest.timezone,
                strip_images=strip_images,
                book=book,
                image_client=image_client,
                image_cache=image_cache,
                image_budget_state=image_budget_state,
                max_images_per_article=max_images_per_article,
                article_file_name=file_name,
            )
            chapter = _make_page(
                title=article.title,
                file_name=file_name,
                body=body,
                language=config.digest.language,
                css=css,
            )
            article_pages.append(chapter)
            book.add_item(chapter)
    finally:
        if owns_image_client and image_client:
            image_client.close()

    directory_page = _make_page(
        title="目录",
        file_name="directory.xhtml",
        body=_directory_body(articles, article_pages, config.categories),
        language=config.digest.language,
        css=css,
    )
    book.add_item(directory_page)

    spine: list[object] = ["nav", cover_page, directory_page]
    spine.extend(article_pages)
    toc_items: list[object] = [cover_page, directory_page]
    toc_items.extend(article_pages)

    if config.digest.include_feed_health_page:
        health_page = _make_page(
            title="订阅源健康状态",
            file_name="feed-health.xhtml",
            body=_feed_health_body(feed_health),
            language=config.digest.language,
            css=css,
        )
        book.add_item(health_page)
        spine.append(health_page)
        toc_items.append(health_page)

    book.toc = toc_items
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(output_path), book, {"epub3_pages": False})


def _make_page(title: str, file_name: str, body: str, language: str, css: epub.EpubItem) -> epub.EpubHtml:
    page = epub.EpubHtml(title=title, file_name=file_name, lang=language)
    page_dir = posixpath.dirname(file_name) or "."
    css_href = posixpath.relpath("style/main.css", start=page_dir)
    page.content = f"""<!DOCTYPE html>
    <html xmlns="http://www.w3.org/1999/xhtml" lang="{escape_attr(language)}">
      <head>
        <title>{escape_attr(title)}</title>
      </head>
      <body>{body}</body>
    </html>
    """
    page.add_link(href=css_href, rel="stylesheet", type="text/css")
    return page


def _article_body(
    article: Article,
    timezone_name: str,
    strip_images: bool,
    book: epub.EpubBook,
    image_client: httpx.Client,
    image_cache: dict[str, str],
    image_budget_state: dict[str, int],
    max_images_per_article: int,
    article_file_name: str,
) -> str:
    content = clean_html(article.content_html, strip_images=strip_images)
    if not strip_images:
        content = _embed_remote_images(
            content,
            base_url=article.url,
            book=book,
            image_client=image_client,
            image_cache=image_cache,
            image_budget_state=image_budget_state,
            max_images_per_article=max_images_per_article,
            article_file_name=article_file_name,
        )
    original_link = ""
    if article.url:
        original_link = f'<p class="original"><a href="{escape_attr(article.url)}">阅读原文</a></p>'
    return f"""
    <article>
      <h1>{escape_attr(article.title)}</h1>
      <p class="meta">来源：{escape_attr(article.feed_name)} ｜ 发布时间：{escape_attr(format_datetime(article.published_at, timezone_name))}</p>
      {original_link}
      <div class="content">{content}</div>
    </article>
    """


def _embed_remote_images(
    html: str,
    base_url: str | None,
    book: epub.EpubBook,
    image_client: httpx.Client,
    image_cache: dict[str, str],
    image_budget_state: dict[str, int],
    max_images_per_article: int,
    article_file_name: str,
) -> str:
    soup = BeautifulSoup(html, "html.parser")
    article_dir = posixpath.dirname(article_file_name)
    embedded_in_article = 0

    for img in list(soup.find_all("img")):
        if embedded_in_article >= max_images_per_article:
            img.decompose()
            continue
        src = img.get("src")
        if not src:
            img.decompose()
            continue
        absolute_url = urljoin(base_url or "", src)
        if not absolute_url.startswith(("http://", "https://")):
            img.decompose()
            continue

        image_path = image_cache.get(absolute_url)
        if image_path is None:
            downloaded = _download_image(absolute_url, image_client)
            if downloaded is None:
                logger.info("Skipping image that could not be embedded: %s", absolute_url)
                img.decompose()
                continue
            data, media_type = downloaded
            if image_budget_state["used"] + len(data) > image_budget_state["limit"]:
                logger.info("Skipping image because EPUB image budget is exhausted: %s", absolute_url)
                img.decompose()
                continue
            digest = hashlib.sha256(absolute_url.encode("utf-8")).hexdigest()[:16]
            extension = _image_extension(media_type, absolute_url)
            image_path = f"images/{digest}{extension}"
            book.add_item(
                epub.EpubItem(
                    uid=f"image-{digest}",
                    file_name=image_path,
                    media_type=media_type,
                    content=data,
                )
            )
            image_cache[absolute_url] = image_path
            image_budget_state["used"] += len(data)

        img["src"] = posixpath.relpath(image_path, start=article_dir or ".")
        embedded_in_article += 1

    return str(soup)


def _download_image(url: str, image_client: httpx.Client) -> tuple[bytes, str] | None:
    try:
        response = image_client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Failed to download image %s: %s", url, exc)
        return None

    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not media_type or media_type == "application/octet-stream":
        guessed_type, _ = mimetypes.guess_type(url)
        media_type = guessed_type or ""
    if media_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        logger.info("Skipping unsupported image type for %s: %s", url, media_type or "unknown")
        return None
    if len(response.content) > 5 * 1024 * 1024:
        logger.warning("Skipping image larger than 5MB: %s", url)
        return None
    return response.content, media_type


def _image_extension(media_type: str, url: str) -> str:
    if media_type == "image/jpeg":
        return ".jpg"
    if media_type == "image/png":
        return ".png"
    if media_type == "image/gif":
        return ".gif"
    if media_type == "image/webp":
        return ".webp"
    suffix = Path(url).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else ".img"


def _image_budget_bytes(max_epub_bytes: int, configured_budget_mb: int | None) -> int:
    if configured_budget_mb is not None:
        return min(configured_budget_mb * 1024 * 1024, int(max_epub_bytes * 0.9))
    return min(120 * 1024 * 1024, int(max_epub_bytes * 0.8))


def _directory_body(
    articles: list[Article],
    article_pages: list[epub.EpubHtml],
    categories: list[str],
) -> str:
    if not articles:
        return "<h1>目录</h1><p>今天没有新文章。</p>"

    page_by_id = {article.article_id: page for article, page in zip(articles, article_pages, strict=True)}
    grouped: dict[str, list[Article]] = defaultdict(list)
    for article in articles:
        grouped[article.category].append(article)

    parts = ["<h1>目录</h1>"]
    ordered_categories = [category for category in categories if grouped.get(category)]
    ordered_categories.extend(sorted(category for category in grouped if category not in categories))
    for category in ordered_categories:
        parts.append(f"<h2>{escape_attr(category)}</h2><ol>")
        for article in grouped[category]:
            page = page_by_id[article.article_id]
            parts.append(
                f'<li><a href="{escape_attr(page.file_name)}">{escape_attr(article.title)}</a>'
                f" <span class=\"source\">{escape_attr(article.feed_name)}</span></li>"
            )
        parts.append("</ol>")
    return "".join(parts)


def _feed_health_body(feed_health: list[dict[str, object]]) -> str:
    if not feed_health:
        return "<h1>订阅源健康状态</h1><p>暂无状态记录。</p>"
    rows = [
        "<h1>订阅源健康状态</h1>",
        "<table><thead><tr><th>Feed</th><th>最后成功</th><th>失败次数</th><th>错误</th></tr></thead><tbody>",
    ]
    for item in feed_health:
        rows.append(
            "<tr>"
            f"<td>{escape_attr(str(item.get('feed_id', '')))}</td>"
            f"<td>{escape_attr(str(item.get('last_success_at') or ''))}</td>"
            f"<td>{escape_attr(str(item.get('failure_count') or 0))}</td>"
            f"<td>{escape_attr(str(item.get('last_error') or ''))}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def _article_file_name(index: int, article: Article, used: set[str]) -> str:
    base = sanitize_filename(f"{index:03d}-{article.article_id[:12]}", fallback=f"article-{index:03d}")
    file_name = f"articles/{base}.xhtml"
    counter = 2
    while file_name in used:
        file_name = f"articles/{base}-{counter}.xhtml"
        counter += 1
    used.add(file_name)
    return file_name


def _css() -> str:
    return """
    body {
      font-family: serif;
      line-height: 1.55;
      color: #111;
      margin: 0 5%;
    }
    h1, h2, h3 {
      line-height: 1.25;
    }
    .cover {
      margin-top: 25%;
      text-align: center;
    }
    .meta, .source, .original {
      color: #555;
      font-size: 0.9em;
    }
    img {
      max-width: 100%;
      height: auto;
    }
    table {
      border-collapse: collapse;
      width: 100%;
    }
    th, td {
      border: 1px solid #aaa;
      padding: 0.35em;
      vertical-align: top;
    }
    pre, code {
      font-family: monospace;
      white-space: pre-wrap;
    }
    blockquote {
      border-left: 0.2em solid #aaa;
      margin-left: 0;
      padding-left: 1em;
      color: #333;
    }
    """

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

from ebooklib import epub

from .article_extractor import clean_html
from .config import AppConfig
from .models import Article
from .utils import escape_attr, format_datetime, html_to_text, sanitize_filename

logger = logging.getLogger(__name__)


class EpubSizeError(RuntimeError):
    pass


def build_epub(
    articles: list[Article],
    config: AppConfig,
    output: str | Path = "output",
    digest_date: date | None = None,
    feed_health: list[dict[str, object]] | None = None,
) -> Path:
    digest_date = digest_date or date.today()
    output_path = _resolve_output_path(Path(output), digest_date)
    max_bytes = config.digest.max_epub_mb * 1024 * 1024
    current_articles = list(articles)
    strip_images = False

    while True:
        _write_epub(current_articles, config, output_path, digest_date, feed_health or [], strip_images=strip_images)
        size = output_path.stat().st_size
        logger.info("Generated EPUB: %s (%s bytes)", output_path, size)
        if size <= max_bytes:
            return output_path

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
    for index, article in enumerate(articles, start=1):
        file_name = _article_file_name(index, article, used_file_names)
        body = _article_body(article, config.digest.timezone, strip_images=strip_images)
        chapter = _make_page(
            title=article.title,
            file_name=file_name,
            body=body,
            language=config.digest.language,
            css=css,
        )
        article_pages.append(chapter)
        book.add_item(chapter)

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
    page.content = f"""<?xml version="1.0" encoding="utf-8"?>
    <!DOCTYPE html>
    <html xmlns="http://www.w3.org/1999/xhtml" lang="{escape_attr(language)}">
      <head>
        <title>{escape_attr(title)}</title>
        <link rel="stylesheet" type="text/css" href="style/main.css" />
      </head>
      <body>{body}</body>
    </html>
    """
    page.add_item(css)
    return page


def _article_body(article: Article, timezone_name: str, strip_images: bool) -> str:
    content = clean_html(article.content_html, strip_images=strip_images)
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

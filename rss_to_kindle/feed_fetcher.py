from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import feedparser
import httpx

from .article_extractor import ArticleExtractor, clean_html
from .config import AppConfig, FeedConfig, selected_feeds
from .models import Article, FeedFetchResult
from .state import StateStore
from .utils import article_matches_keywords, compute_article_id, html_to_text, parse_datetime, today_in_timezone, utcnow

logger = logging.getLogger(__name__)


class FeedFetcher:
    def __init__(self, timeout: float = 20.0, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "rss-to-kindle/0.1"},
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def fetch_feed(
        self,
        feed: FeedConfig,
        state: StateStore | None = None,
        update_health: bool = True,
    ) -> FeedFetchResult:
        headers = state.get_conditional_headers(feed.id) if state else {}
        try:
            response = self.client.get(feed.url, headers=headers)
            if response.status_code == 304:
                if state and update_health:
                    state.update_feed_success(feed.id, response.headers.get("etag"), response.headers.get("last-modified"))
                logger.info("Feed not modified: %s", feed.id)
                return FeedFetchResult(
                    feed_id=feed.id,
                    feed_name=feed.name,
                    status_code=response.status_code,
                    parsed_title=None,
                    articles=[],
                    not_modified=True,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            if state and update_health:
                state.update_feed_failure(feed.id, str(exc))
            raise RuntimeError(f"Failed to fetch feed {feed.id} ({feed.url}): {exc}") from exc

        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            error = str(parsed.get("bozo_exception", "invalid feed"))
            if state and update_health:
                state.update_feed_failure(feed.id, error)
            raise RuntimeError(f"Failed to parse feed {feed.id}: {error}")

        articles = [_entry_to_article(feed, entry) for entry in parsed.entries]
        if state and update_health:
            state.update_feed_success(feed.id, response.headers.get("etag"), response.headers.get("last-modified"))

        parsed_title = parsed.feed.get("title") if parsed.feed else None
        logger.info("Fetched feed %s: %s articles", feed.id, len(articles))
        return FeedFetchResult(
            feed_id=feed.id,
            feed_name=feed.name,
            status_code=response.status_code,
            parsed_title=parsed_title,
            articles=articles,
        )


def collect_digest_articles(
    config: AppConfig,
    state: StateStore,
    include_testing: bool = False,
    limit: int | None = None,
    digest_date: date | None = None,
    extractor: ArticleExtractor | None = None,
    fetcher: FeedFetcher | None = None,
) -> list[Article]:
    own_fetcher = fetcher is None
    own_extractor = extractor is None
    fetcher = fetcher or FeedFetcher()
    extractor = extractor or ArticleExtractor()

    feeds = selected_feeds(config, include_testing=include_testing)
    digest_date = digest_date or today_in_timezone(config.digest.timezone)
    articles: list[Article] = []
    failures = 0
    try:
        for feed in feeds:
            try:
                result = fetcher.fetch_feed(feed, state=state, update_health=True)
            except RuntimeError as exc:
                failures += 1
                logger.error("%s", exc)
                continue

            max_items = feed.max_items or config.digest.default_max_items_per_feed
            kept_for_feed = 0

            for article in result.articles:
                if state.is_article_sent(article.article_id):
                    logger.debug("Skipping already-sent article: %s", article.title)
                    continue
                if not _is_article_for_digest_date(article, digest_date, config.digest.timezone):
                    logger.debug("Skipping article outside digest date or without published_at: %s", article.title)
                    continue

                article.content_html = _resolve_article_content(article, feed, extractor)
                if not article_matches_keywords(
                    article.title,
                    article.summary,
                    article.content_html,
                    feed.include_keywords,
                    feed.exclude_keywords,
                ):
                    continue

                state.upsert_article(article, status="fetched")
                articles.append(article)
                kept_for_feed += 1
                if kept_for_feed >= max_items:
                    break

        if feeds and failures == len(feeds):
            raise RuntimeError("All selected feeds failed to fetch or parse")

        category_order = {category: index for index, category in enumerate(config.categories)}
        articles.sort(
            key=lambda article: (
                category_order.get(article.category, len(category_order)),
                -article.feed_priority,
                -_sort_timestamp(article.published_at),
            )
        )
        return articles[: limit or config.digest.max_total_articles]
    finally:
        if own_fetcher and fetcher:
            fetcher.close()
        if own_extractor and extractor:
            extractor.close()


def _sort_timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _is_article_for_digest_date(article: Article, digest_date: date, timezone_name: str) -> bool:
    if article.published_at is None:
        return False
    published_at = article.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at.astimezone(ZoneInfo(timezone_name)).date() == digest_date


def _resolve_article_content(article: Article, feed: FeedConfig, extractor: ArticleExtractor) -> str:
    fallback = article.content_html or article.summary
    if feed.full_text and article.url:
        extracted = extractor.extract_from_url(article.url, fallback_html=fallback)
        return extracted.html
    return clean_html(fallback)


def _entry_to_article(feed: FeedConfig, entry: feedparser.FeedParserDict) -> Article:
    title = _text(entry.get("title")) or "Untitled"
    url = _entry_url(entry)
    guid = _text(entry.get("id") or entry.get("guid"))
    published_at = (
        parse_datetime(entry.get("published_parsed"))
        or parse_datetime(entry.get("updated_parsed"))
        or parse_datetime(entry.get("published"))
        or parse_datetime(entry.get("updated"))
    )
    content_html = _entry_content(entry)
    summary = _text(entry.get("summary")) or html_to_text(content_html)
    fetched_at = utcnow()
    article_id = compute_article_id(feed.id, guid, url, title, published_at)
    return Article(
        article_id=article_id,
        feed_id=feed.id,
        feed_name=feed.name,
        category=feed.category,
        feed_priority=feed.priority,
        title=title,
        url=url,
        guid=guid,
        published_at=published_at,
        fetched_at=fetched_at,
        summary=summary,
        content_html=content_html,
    )


def _entry_url(entry: feedparser.FeedParserDict) -> str | None:
    if entry.get("link"):
        return str(entry.get("link"))
    links = entry.get("links") or []
    for link in links:
        href = link.get("href") if isinstance(link, dict) else None
        if href:
            return str(href)
    return None


def _entry_content(entry: feedparser.FeedParserDict) -> str:
    content = entry.get("content")
    if content and isinstance(content, list):
        for item in content:
            value = item.get("value") if isinstance(item, dict) else None
            if value:
                return str(value)
    return str(entry.get("summary") or entry.get("description") or "")


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()

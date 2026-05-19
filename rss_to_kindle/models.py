from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class FeedStatus(str, Enum):
    ACTIVE = "active"
    TESTING = "testing"
    PAUSED = "paused"
    ARCHIVED = "archived"
    BROKEN = "broken"


@dataclass(slots=True)
class Article:
    article_id: str
    feed_id: str
    feed_name: str
    category: str
    feed_priority: int
    title: str
    url: str | None
    guid: str | None
    published_at: datetime | None
    fetched_at: datetime
    summary: str
    content_html: str


@dataclass(slots=True)
class FeedFetchResult:
    feed_id: str
    feed_name: str
    status_code: int
    parsed_title: str | None
    articles: list[Article]
    not_modified: bool = False


@dataclass(slots=True)
class ExtractedContent:
    html: str
    used_full_text: bool
    error: str | None = None

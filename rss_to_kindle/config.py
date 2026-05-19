from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .models import FeedStatus


FEED_ID_RE = re.compile(r"^[a-z0-9_-]+$")


class DigestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = "每日 RSS 摘要"
    language: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    default_oldest_hours: int = Field(default=24, gt=0)
    default_max_items_per_feed: int = Field(default=10, gt=0)
    max_total_articles: int = Field(default=80, gt=0)
    max_epub_mb: int = Field(default=45, gt=0)
    include_feed_health_page: bool = True


class DeliveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kindle_email: str
    sender_email: str

    @field_validator("kindle_email", "sender_email")
    @classmethod
    def validate_email_like(cls, value: str) -> str:
        value = value.strip()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("must be an email-like address")
        return value


class FeedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    url: str
    category: str = "Other"
    status: FeedStatus = FeedStatus.ACTIVE
    full_text: bool = False
    max_items: int | None = Field(default=None, gt=0)
    oldest_hours: int | None = Field(default=None, gt=0)
    priority: int = Field(default=50, ge=0, le=100)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not FEED_ID_RE.fullmatch(value):
            raise ValueError("feed.id only allows lowercase letters, numbers, hyphens and underscores")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("feed.url must start with http:// or https://")
        return value

    @field_validator("include_keywords", "exclude_keywords")
    @classmethod
    def normalize_keywords(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    digest: DigestConfig
    delivery: DeliveryConfig
    categories: list[str] = Field(default_factory=lambda: ["Other"])
    feeds: list[FeedConfig]

    @model_validator(mode="after")
    def validate_feeds(self) -> Self:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for feed in self.feeds:
            if feed.id in seen:
                duplicates.add(feed.id)
            seen.add(feed.id)
        if duplicates:
            raise ValueError(f"feed.id must be unique; duplicates: {', '.join(sorted(duplicates))}")

        category_set = set(self.categories)
        missing = sorted({feed.category for feed in self.feeds if feed.category not in category_set})
        if missing:
            raise ValueError(f"feed.category must be listed in categories: {', '.join(missing)}")
        return self


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    try:
        return AppConfig.model_validate(data)
    except ValidationError:
        raise


def selected_feeds(config: AppConfig, include_testing: bool = False) -> list[FeedConfig]:
    allowed = {FeedStatus.ACTIVE}
    if include_testing:
        allowed.add(FeedStatus.TESTING)
    return [feed for feed in config.feeds if feed.status in allowed]

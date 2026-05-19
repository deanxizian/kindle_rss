from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rss_to_kindle.config import load_config


def test_load_example_config() -> None:
    config = load_config(Path(__file__).parents[1] / "feeds.example.yml")

    assert config.version == 1
    assert len(config.feeds) == 2
    assert config.feeds[0].id == "ruanyifeng"


def test_feed_id_must_be_unique(tmp_path: Path) -> None:
    data = _base_config()
    data["feeds"].append({**data["feeds"][0], "name": "Duplicate"})
    path = tmp_path / "feeds.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        load_config(path)


def test_status_enum_validation(tmp_path: Path) -> None:
    data = _base_config()
    data["feeds"][0]["status"] = "unknown"
    path = tmp_path / "feeds.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(path)


def _base_config() -> dict:
    return {
        "version": 1,
        "digest": {
            "title": "每日 RSS 摘要",
            "language": "zh-CN",
            "timezone": "Asia/Shanghai",
            "default_oldest_hours": 24,
            "default_max_items_per_feed": 10,
            "max_total_articles": 80,
            "max_epub_mb": 45,
            "include_feed_health_page": True,
        },
        "delivery": {
            "kindle_email": "kindle@example.com",
            "sender_email": "sender@example.com",
        },
        "categories": ["Tech", "Other"],
        "feeds": [
            {
                "id": "demo",
                "name": "Demo",
                "url": "https://example.com/feed.xml",
                "category": "Tech",
                "status": "active",
                "full_text": False,
                "priority": 50,
                "include_keywords": [],
                "exclude_keywords": [],
            }
        ],
    }

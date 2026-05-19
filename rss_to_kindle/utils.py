from __future__ import annotations

import hashlib
import json
import re
from calendar import timegm
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from time import struct_time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_in_timezone(timezone_name: str) -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


def parse_digest_date(value: str | None, timezone_name: str) -> date:
    if value:
        return date.fromisoformat(value)
    return today_in_timezone(timezone_name)


def parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, struct_time) or (
        isinstance(value, tuple) and len(value) >= 9 and all(isinstance(item, int) for item in value[:6])
    ):
        return datetime.fromtimestamp(timegm(value), timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, IndexError):
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def format_datetime(value: datetime | None, timezone_name: str = "Asia/Shanghai") -> str:
    if value is None:
        return "未知时间"
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")


def normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_PARAMS or lowered.startswith("utm_"):
            continue
        query_items.append((key, value))
    query = urlencode(query_items, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def compute_article_id(
    feed_id: str,
    guid: str | None,
    url: str | None,
    title: str,
    published_at: datetime | str | None,
) -> str:
    if guid:
        source = f"{feed_id}{guid.strip()}"
    elif url:
        source = f"{feed_id}{normalize_url(url)}"
    else:
        published_text = published_at.isoformat() if isinstance(published_at, datetime) else str(published_at or "")
        source = f"{feed_id}{title}{published_text}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)


def article_matches_keywords(
    title: str,
    summary: str,
    content_html: str,
    include_keywords: list[str],
    exclude_keywords: list[str],
) -> bool:
    haystack = " ".join([title or "", summary or "", html_to_text(content_html)]).casefold()
    if include_keywords:
        if not any(keyword.casefold() in haystack for keyword in include_keywords):
            return False
    if exclude_keywords:
        if any(keyword.casefold() in haystack for keyword in exclude_keywords):
            return False
    return True


def sanitize_filename(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return cleaned or fallback


def escape_attr(value: str | None) -> str:
    return escape(value or "", quote=True)


def manifest_path_for_epub(epub_path: Path) -> Path:
    return epub_path.with_suffix(".manifest.json")


def write_manifest(epub_path: Path, digest_date: date, article_ids: list[str]) -> Path:
    manifest_path = manifest_path_for_epub(epub_path)
    payload = {
        "digest_date": digest_date.isoformat(),
        "epub_path": str(epub_path),
        "article_ids": article_ids,
        "article_count": len(article_ids),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def read_manifest(epub_path: Path) -> dict[str, object] | None:
    manifest_path = manifest_path_for_epub(epub_path)
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))

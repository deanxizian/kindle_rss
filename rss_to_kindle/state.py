from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .models import Article
from .utils import utcnow

DEFAULT_STATE_DB = Path(".rss_to_kindle/state.db")


class StateStore:
    def __init__(self, path: str | Path | None = None) -> None:
        env_path = os.getenv("RSS2KINDLE_STATE_DB")
        self.path = Path(path or env_path or DEFAULT_STATE_DB)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS feeds_state (
                    feed_id TEXT PRIMARY KEY,
                    last_fetched_at TEXT,
                    last_success_at TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    etag TEXT,
                    last_modified TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS articles (
                    article_id TEXT PRIMARY KEY,
                    feed_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    guid TEXT,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    sent_at TEXT,
                    status TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_articles_feed_id ON articles(feed_id);
                CREATE INDEX IF NOT EXISTS idx_articles_sent_at ON articles(sent_at);
                CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);

                CREATE TABLE IF NOT EXISTS delivery_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    digest_date TEXT NOT NULL,
                    epub_path TEXT NOT NULL,
                    article_count INTEGER NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    sent_at TEXT,
                    status TEXT NOT NULL,
                    error TEXT
                );
                """
            )

    def get_conditional_headers(self, feed_id: str) -> dict[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT etag, last_modified FROM feeds_state WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
        headers: dict[str, str] = {}
        if not row:
            return headers
        if row["etag"]:
            headers["If-None-Match"] = row["etag"]
        if row["last_modified"]:
            headers["If-Modified-Since"] = row["last_modified"]
        return headers

    def update_feed_success(self, feed_id: str, etag: str | None = None, last_modified: str | None = None) -> None:
        now = utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feeds_state (
                    feed_id, last_fetched_at, last_success_at, failure_count,
                    last_error, etag, last_modified, updated_at
                )
                VALUES (?, ?, ?, 0, NULL, ?, ?, ?)
                ON CONFLICT(feed_id) DO UPDATE SET
                    last_fetched_at = excluded.last_fetched_at,
                    last_success_at = excluded.last_success_at,
                    failure_count = 0,
                    last_error = NULL,
                    etag = COALESCE(excluded.etag, feeds_state.etag),
                    last_modified = COALESCE(excluded.last_modified, feeds_state.last_modified),
                    updated_at = excluded.updated_at
                """,
                (feed_id, now, now, etag, last_modified, now),
            )

    def update_feed_failure(self, feed_id: str, error: str) -> None:
        now = utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feeds_state (
                    feed_id, last_fetched_at, last_success_at, failure_count,
                    last_error, updated_at
                )
                VALUES (?, ?, NULL, 1, ?, ?)
                ON CONFLICT(feed_id) DO UPDATE SET
                    last_fetched_at = excluded.last_fetched_at,
                    failure_count = feeds_state.failure_count + 1,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (feed_id, now, error[:2000], now),
            )

    def upsert_article(self, article: Article, status: str = "fetched") -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO articles (
                    article_id, feed_id, title, url, guid, published_at,
                    fetched_at, sent_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    guid = excluded.guid,
                    published_at = excluded.published_at,
                    fetched_at = excluded.fetched_at,
                    status = CASE
                        WHEN articles.sent_at IS NOT NULL THEN articles.status
                        ELSE excluded.status
                    END
                """,
                (
                    article.article_id,
                    article.feed_id,
                    article.title,
                    article.url,
                    article.guid,
                    article.published_at.isoformat() if article.published_at else None,
                    article.fetched_at.isoformat(),
                    status,
                ),
            )

    def is_article_sent(self, article_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT sent_at, status FROM articles WHERE article_id = ?",
                (article_id,),
            ).fetchone()
        return bool(row and (row["sent_at"] or row["status"] == "sent"))

    def mark_articles_sent(self, article_ids: list[str]) -> None:
        if not article_ids:
            return
        now = utcnow().isoformat()
        placeholders = ",".join("?" for _ in article_ids)
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE articles
                SET sent_at = ?, status = 'sent'
                WHERE article_id IN ({placeholders})
                """,
                [now, *article_ids],
            )

    def log_delivery(
        self,
        digest_date: str,
        epub_path: str,
        article_count: int,
        file_size_bytes: int,
        status: str,
        error: str | None = None,
        sent_at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO delivery_logs (
                    digest_date, epub_path, article_count, file_size_bytes,
                    sent_at, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (digest_date, epub_path, article_count, file_size_bytes, sent_at, status, error),
            )

    def get_feed_states(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT feed_id, last_fetched_at, last_success_at, failure_count,
                       last_error, etag, last_modified, updated_at
                FROM feeds_state
                ORDER BY feed_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_delivery(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, digest_date, epub_path, article_count, file_size_bytes,
                       sent_at, status, error
                FROM delivery_logs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def get_recent_failed_feeds(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT feed_id, failure_count, last_error, updated_at
                FROM feeds_state
                WHERE failure_count > 0
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

from __future__ import annotations

import logging
import mimetypes
import os
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from .config import AppConfig
from .state import StateStore
from .utils import utcnow

logger = logging.getLogger(__name__)


class MailConfigError(RuntimeError):
    pass


def send_epub(
    epub_path: str | Path,
    config: AppConfig,
    state: StateStore | None = None,
    article_ids: list[str] | None = None,
    digest_date: date | None = None,
    article_count: int | None = None,
) -> None:
    path = Path(epub_path)
    digest_date = digest_date or date.today()
    article_ids = article_ids or []
    article_count = article_count if article_count is not None else len(article_ids)

    if not path.exists():
        _log_failed(state, digest_date, path, article_count, 0, f"EPUB file not found: {path}")
        raise FileNotFoundError(f"EPUB file not found: {path}")

    file_size = path.stat().st_size
    max_bytes = config.digest.max_epub_mb * 1024 * 1024
    if file_size > max_bytes:
        error = f"EPUB file exceeds max_epub_mb={config.digest.max_epub_mb}: {file_size} bytes"
        _log_failed(state, digest_date, path, article_count, file_size, error)
        raise ValueError(error)

    settings = _smtp_settings()
    message = EmailMessage()
    message["Subject"] = f"{config.digest.title} - {digest_date.isoformat()}"
    message["From"] = settings["sender_email"]
    message["To"] = settings["kindle_email"]
    message.set_content("RSS daily digest attached.")

    mime_type, _ = mimetypes.guess_type(path.name)
    maintype, subtype = (mime_type or "application/epub+zip").split("/", 1)
    message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    try:
        with smtplib.SMTP(settings["host"], int(settings["port"]), timeout=30) as smtp:
            if settings["use_tls"]:
                smtp.starttls()
            smtp.login(settings["user"], settings["password"])
            smtp.send_message(message)
    except Exception as exc:
        logger.error("Failed to send EPUB to Kindle: %s", exc)
        _log_failed(state, digest_date, path, article_count, file_size, str(exc))
        raise

    sent_at = utcnow().isoformat()
    if state:
        state.mark_articles_sent(article_ids)
        state.log_delivery(
            digest_date=digest_date.isoformat(),
            epub_path=str(path),
            article_count=article_count,
            file_size_bytes=file_size,
            sent_at=sent_at,
            status="sent",
        )
    logger.info("Sent EPUB to Kindle: %s", path)


def _smtp_settings() -> dict[str, object]:
    settings = {
        "host": os.getenv("SMTP_HOST", ""),
        "port": os.getenv("SMTP_PORT", "587"),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASS", ""),
        "use_tls": _env_bool(os.getenv("SMTP_USE_TLS") or "true"),
        "kindle_email": os.getenv("KINDLE_EMAIL", ""),
        "sender_email": os.getenv("SENDER_EMAIL", ""),
    }
    missing = [key for key in ("host", "port", "user", "password", "kindle_email", "sender_email") if not settings[key]]
    if missing:
        raise MailConfigError(f"Missing SMTP/Kindle settings: {', '.join(missing)}")
    return settings


def _env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _log_failed(
    state: StateStore | None,
    digest_date: date,
    epub_path: Path,
    article_count: int,
    file_size_bytes: int,
    error: str,
) -> None:
    if state:
        state.log_delivery(
            digest_date=digest_date.isoformat(),
            epub_path=str(epub_path),
            article_count=article_count,
            file_size_bytes=file_size_bytes,
            sent_at=None,
            status="failed",
            error=error[:2000],
        )

from __future__ import annotations

import logging
import re
from html import escape

import httpx
import trafilatura
from bs4 import BeautifulSoup, Comment

from .models import ExtractedContent

logger = logging.getLogger(__name__)

BLOCK_SELECTOR_RE = re.compile(r"(ad-|advert|comment|comments|promo|recommend|share|social|subscribe)", re.I)
ALLOWED_TAGS = {
    "h1",
    "h2",
    "h3",
    "p",
    "blockquote",
    "ul",
    "ol",
    "li",
    "pre",
    "code",
    "img",
    "a",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "strong",
    "em",
    "b",
    "i",
    "br",
    "hr",
}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}


class ArticleExtractor:
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

    def extract_from_url(self, url: str, fallback_html: str | None = None) -> ExtractedContent:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            extracted = trafilatura.extract(
                response.text,
                url=str(response.url),
                output_format="html",
                include_images=True,
                include_links=True,
                include_tables=True,
            )
            if extracted:
                return ExtractedContent(html=clean_html(extracted), used_full_text=True)
            logger.info("Full-text extraction returned empty content for %s; using feed summary", url)
            return ExtractedContent(html=clean_html(fallback_html), used_full_text=False, error="empty extraction")
        except Exception as exc:  # trafilatura/httpx can raise several concrete exception types.
            logger.warning("Full-text extraction failed for %s: %s", url, exc)
            return ExtractedContent(html=clean_html(fallback_html), used_full_text=False, error=str(exc))


def clean_html(html: str | None, strip_images: bool = False) -> str:
    if not html:
        return "<p>无正文内容。</p>"

    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag in soup(["script", "style", "nav", "footer", "form", "iframe", "header", "aside"]):
        tag.decompose()

    for tag in list(soup.find_all(True)):
        marker = " ".join(
            [
                str(tag.get("id", "")),
                " ".join(tag.get("class", [])) if isinstance(tag.get("class"), list) else str(tag.get("class", "")),
            ]
        )
        if marker and BLOCK_SELECTOR_RE.search(marker):
            tag.decompose()

    for tag in list(soup.find_all(True)):
        if tag.name is None:
            continue
        if strip_images and tag.name == "img":
            tag.decompose()
            continue
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        allowed_attrs = ALLOWED_ATTRS.get(tag.name, set())
        for attr in list(tag.attrs):
            if attr not in allowed_attrs:
                del tag.attrs[attr]
        if tag.name == "a":
            href = tag.get("href", "")
            if href.lower().startswith(("javascript:", "data:")):
                tag.unwrap()
        if tag.name == "img":
            src = tag.get("src", "")
            if not src or src.lower().startswith("data:"):
                tag.decompose()

    for tag in list(soup.find_all(["p", "li", "blockquote"])):
        if not tag.get_text(strip=True) and not tag.find("img"):
            tag.decompose()

    body = soup.body or soup
    content = "".join(str(child) for child in body.children).strip()
    if content:
        return content

    text = soup.get_text(" ", strip=True)
    return f"<p>{escape(text)}</p>" if text else "<p>无正文内容。</p>"


def strip_images_from_html(html: str) -> str:
    return clean_html(html, strip_images=True)

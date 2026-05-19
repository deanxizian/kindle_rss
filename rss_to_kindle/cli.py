from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

import typer
from pydantic import ValidationError

from .article_extractor import ArticleExtractor
from .config import AppConfig, load_config
from .epub_builder import build_epub
from .feed_fetcher import FeedFetcher, collect_digest_articles
from .mailer import send_epub
from .state import StateStore
from .utils import format_datetime, parse_digest_date, read_manifest, write_manifest

app = typer.Typer(no_args_is_help=True)
logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _load_config_or_exit(config: Path) -> AppConfig:
    try:
        return load_config(config)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"配置错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("config-check")
def config_check(config: Path = typer.Option(Path("feeds.yml"), "--config", "-c")) -> None:
    """校验配置文件。"""
    _setup_logging()
    cfg = _load_config_or_exit(config)
    counts = Counter(feed.status.value for feed in cfg.feeds)
    typer.echo(f"配置文件有效：{config}")
    typer.echo(f"订阅源总数：{len(cfg.feeds)}")
    for status in ("active", "testing", "paused", "archived", "broken"):
        typer.echo(f"{status}: {counts.get(status, 0)}")


@app.command("feed-list")
def feed_list(config: Path = typer.Option(Path("feeds.yml"), "--config", "-c")) -> None:
    """列出订阅源。"""
    _setup_logging()
    cfg = _load_config_or_exit(config)
    typer.echo("id\tname\tcategory\tstatus\tpriority\turl")
    for feed in cfg.feeds:
        typer.echo(f"{feed.id}\t{feed.name}\t{feed.category}\t{feed.status.value}\t{feed.priority}\t{feed.url}")


@app.command("feed-test")
def feed_test(feed_id: str, config: Path = typer.Option(Path("feeds.yml"), "--config", "-c")) -> None:
    """拉取并测试指定订阅源。"""
    _setup_logging()
    cfg = _load_config_or_exit(config)
    feed = next((item for item in cfg.feeds if item.id == feed_id), None)
    if not feed:
        typer.echo(f"未找到订阅源：{feed_id}", err=True)
        raise typer.Exit(code=1)

    state = StateStore()
    fetcher = FeedFetcher()
    extractor = ArticleExtractor()
    try:
        result = fetcher.fetch_feed(feed, state=state, update_health=True)
        typer.echo(f"Feed: {feed.name}")
        typer.echo(f"HTTP 状态码: {result.status_code}")
        typer.echo(f"解析标题: {result.parsed_title or '-'}")
        typer.echo(f"文章数量: {len(result.articles)}")
        if result.articles:
            latest = result.articles[0]
            typer.echo(f"最新文章: {latest.title}")
            typer.echo(f"最新发布时间: {format_datetime(latest.published_at, cfg.digest.timezone)}")
            if latest.url:
                extracted = extractor.extract_from_url(latest.url, fallback_html=latest.content_html or latest.summary)
                status = "ok" if extracted.used_full_text else f"fallback ({extracted.error or 'no full text'})"
                typer.echo(f"全文抽取: {status}")
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        fetcher.close()
        extractor.close()


@app.command("build")
def build(
    config: Path = typer.Option(Path("feeds.yml"), "--config", "-c"),
    output: Path = typer.Option(Path("output"), "--output", "-o"),
    include_testing: bool = typer.Option(False, "--include-testing"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    date_value: str | None = typer.Option(None, "--date", help="日报日期，格式 YYYY-MM-DD"),
    limit: int | None = typer.Option(None, "--limit", min=1),
) -> None:
    """抓取文章并构建 EPUB。"""
    _setup_logging()
    cfg = _load_config_or_exit(config)
    digest_date = parse_digest_date(date_value, cfg.digest.timezone)
    state = StateStore()
    articles = collect_digest_articles(
        cfg,
        state=state,
        include_testing=include_testing,
        limit=limit,
        digest_date=digest_date,
    )

    if dry_run:
        typer.echo(f"将收录文章数：{len(articles)}")
        for article in articles:
            typer.echo(f"- [{article.category}] {article.feed_name}: {article.title}")
        return

    epub_path = build_epub(articles, cfg, output=output, digest_date=digest_date, feed_health=state.get_feed_states())
    manifest_path = write_manifest(epub_path, digest_date, [article.article_id for article in articles])
    typer.echo(f"EPUB: {epub_path}")
    typer.echo(f"Manifest: {manifest_path}")


@app.command("send")
def send(
    epub_path: Path,
    config: Path = typer.Option(Path("feeds.yml"), "--config", "-c"),
) -> None:
    """发送指定 EPUB 到 Kindle 邮箱。"""
    _setup_logging()
    cfg = _load_config_or_exit(config)
    state = StateStore()
    manifest = read_manifest(epub_path) or {}
    article_ids = [str(item) for item in manifest.get("article_ids", [])] if manifest else []
    digest_date = parse_digest_date(str(manifest.get("digest_date")) if manifest.get("digest_date") else None, cfg.digest.timezone)
    article_count = int(manifest.get("article_count", len(article_ids))) if manifest else len(article_ids)
    send_epub(epub_path, cfg, state=state, article_ids=article_ids, digest_date=digest_date, article_count=article_count)
    typer.echo(f"已发送：{epub_path}")


@app.command("run")
def run(config: Path = typer.Option(Path("feeds.yml"), "--config", "-c")) -> None:
    """抓取、构建并发送 Kindle 日报。"""
    _setup_logging()
    cfg = _load_config_or_exit(config)
    digest_date = parse_digest_date(None, cfg.digest.timezone)
    state = StateStore()
    articles = collect_digest_articles(cfg, state=state, include_testing=False, digest_date=digest_date)
    if not articles:
        state.log_delivery(
            digest_date=digest_date.isoformat(),
            epub_path="",
            article_count=0,
            file_size_bytes=0,
            status="skipped",
            error="No new articles",
        )
        typer.echo("没有新文章，跳过生成和发送。")
        return

    epub_path = build_epub(articles, cfg, output=Path("output"), digest_date=digest_date, feed_health=state.get_feed_states())
    write_manifest(epub_path, digest_date, [article.article_id for article in articles])
    send_epub(
        epub_path,
        cfg,
        state=state,
        article_ids=[article.article_id for article in articles],
        digest_date=digest_date,
        article_count=len(articles),
    )
    typer.echo(f"完成：{epub_path}")


@app.command("status")
def status(config: Path = typer.Option(Path("feeds.yml"), "--config", "-c")) -> None:
    """查看订阅源和投递状态。"""
    _setup_logging()
    _load_config_or_exit(config)
    state = StateStore()
    feed_states = state.get_feed_states()
    typer.echo("订阅源健康状态：")
    if not feed_states:
        typer.echo("暂无状态记录。")
    for item in feed_states:
        typer.echo(
            f"{item['feed_id']}\tlast_success={item.get('last_success_at') or '-'}"
            f"\tfailures={item.get('failure_count') or 0}\terror={item.get('last_error') or '-'}"
        )

    recent = state.get_recent_delivery()
    typer.echo("\n最近一次投递：")
    typer.echo(recent if recent else "暂无投递记录。")

    failed = state.get_recent_failed_feeds()
    typer.echo("\n最近失败的 feed：")
    if not failed:
        typer.echo("暂无失败记录。")
    for item in failed:
        typer.echo(f"{item['feed_id']}\tfailures={item['failure_count']}\terror={item['last_error']}")


if __name__ == "__main__":
    app()

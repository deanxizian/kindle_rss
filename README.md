# RSS to Kindle Daily Digest

一个无 Web UI 的 Python 工具：每天读取 `feeds.yml` 中的 RSS/Atom 订阅源，抓取新文章，生成一本 EPUB 日报，并通过 Send to Kindle Email 发送到 Kindle。

## 功能

- 使用 `feeds.yml` 管理订阅源，订阅配置和运行状态分离。
- 支持 RSS 和 Atom，使用 `feedparser` 解析。
- 默认只收录 `active` 源，CLI 可选择包含 `testing` 源。
- 只收录明确带发布时间且落在抓取窗口内的文章；默认窗口是运行时刻往前 24 小时，没有发布时间的条目会被跳过。
- 使用 SQLite 保存状态：订阅源健康、文章去重、投递日志。
- 使用 `trafilatura` 抽取全文，失败后降级使用 RSS content/summary。
- 使用 `ebooklib` 生成每日一本 EPUB，并尽量下载文章图片嵌入 EPUB。
- 使用 SMTP 发送 EPUB 附件到 Kindle 邮箱。
- 提供 CLI 和 GitHub Actions 示例。

## 安装

需要 Python 3.11+。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 配置订阅源

复制示例配置：

```bash
cp feeds.example.yml feeds.yml
```

编辑 `feeds.yml`，添加或修改订阅源：

```yaml
feeds:
  - id: "example"
    name: "Example Feed"
    url: "https://example.com/feed.xml"
    category: "Tech"
    status: "active"
    full_text: true
    max_items: 10
    oldest_hours: 24
    priority: 80
    include_keywords: []
    exclude_keywords: []
```

规则：

- `feed.id` 必须唯一，只允许小写字母、数字、短横线和下划线。
- `status=active` 默认参与日报。
- `status=testing` 默认不投递，可用 `--include-testing` 手动参与构建。
- `paused`、`archived`、`broken` 不参与默认构建。
- 文章必须有可解析发布时间，并且发布时间要落在抓取窗口内；默认窗口是运行时刻往前 `digest.default_oldest_hours` 小时。
- 未设置 `max_items`、`oldest_hours` 时使用 `digest` 默认值。
- `include_keywords` 非空时，标题、摘要或正文至少命中一个关键词才收录。
- `exclude_keywords` 命中时排除。

## 配置邮件投递

复制环境变量示例：

```bash
cp .env.example .env
```

实际运行前把变量导入当前 shell，或在系统/VPS/GitHub Actions 中配置：

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_USER=your-smtp-user@example.com
export SMTP_PASS=your-smtp-password
export SMTP_USE_TLS=true
export KINDLE_EMAIL=your-kindle-email@kindle.com
export SENDER_EMAIL=your-approved-sender@example.com
```

发送命令只从环境变量读取 SMTP、Kindle 收件邮箱和发件邮箱。`feeds.yml` 中的 `delivery` 字段用于配置校验和示例说明，不作为发送时的凭据来源。

Amazon Send to Kindle 邮件投递前置条件：

- 你需要知道自己的 Kindle 邮箱。
- `SENDER_EMAIL` 必须在 Amazon 的 Approved Personal Document Email List 中授权。
- 不要把 SMTP 密码写入 `feeds.yml` 或提交到仓库；`.env` 已加入 `.gitignore`。

## 状态库

默认状态库路径：

```text
.rss_to_kindle/state.db
```

也可以用环境变量覆盖：

```bash
export RSS2KINDLE_STATE_DB=/path/to/state.db
```

SQLite 会记录：

- `feeds_state`：订阅源抓取健康状态、失败次数、ETag、Last-Modified。
- `articles`：文章 ID、来源、发布时间、发送状态。
- `delivery_logs`：EPUB 投递记录。

## CLI 使用

校验配置：

```bash
python -m rss_to_kindle.cli config-check --config feeds.yml
```

列出订阅源：

```bash
python -m rss_to_kindle.cli feed-list --config feeds.yml
```

测试单个订阅源：

```bash
python -m rss_to_kindle.cli feed-test ruanyifeng --config feeds.yml
```

手动生成 EPUB：

```bash
python -m rss_to_kindle.cli build --config feeds.yml --output output/
```

包含 testing 源并指定 EPUB 日期：

```bash
python -m rss_to_kindle.cli build --config feeds.yml --output output/ --include-testing --date 2026-05-19
```

`--date` 只控制 EPUB 文件名、标题和发送主题中的日期；文章筛选仍按运行时刻往前 `oldest_hours` 小时计算。默认配置下，GitHub Actions 在北京时间 06:00 运行时，会收录过去 24 小时内发布的文章。

只预览将收录的文章，不生成 EPUB：

```bash
python -m rss_to_kindle.cli build --config feeds.yml --include-testing --dry-run
```

图片说明：构建 EPUB 时会尝试下载文章 HTML 中的远程图片，并改写为 EPUB 内部图片资源。默认每篇文章最多保留前 8 张图片，可通过 `digest.max_images_per_article` 调整；全书图片预算默认按 `max_epub_mb` 自动估算，也可用 `digest.max_image_budget_mb` 明确设置。不支持的格式，例如 AVIF，会被跳过。

发送 EPUB 到 Kindle：

```bash
python -m rss_to_kindle.cli send output/daily-rss-2026-05-19.epub --config feeds.yml
```

一键运行：

```bash
python -m rss_to_kindle.cli run --config feeds.yml
```

查看状态：

```bash
python -m rss_to_kindle.cli status --config feeds.yml
```

## GitHub Actions

`.github/workflows/daily.yml` 提供了每日自动运行示例：

- 支持 `workflow_dispatch` 手动触发。
- 使用 UTC cron；示例中 `22:00 UTC` 等于北京时间次日 `06:00`，运行时会收录过去 24 小时内发布的文章。
- 安装依赖后运行 `config-check` 和 `run`。
- 上传 `output/` 和 `.rss_to_kindle/state.db` 为 artifact。

需要在 GitHub 仓库 Secrets 中配置：

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_USE_TLS`
- `KINDLE_EMAIL`
- `SENDER_EMAIL`

注意：GitHub Actions 的工作目录默认是临时环境，SQLite 状态库不会天然跨天持久化。当前 workflow 会上传 `.rss_to_kindle/state.db` artifact，但不会自动提交到仓库。如需跨天稳定去重，可以自行使用 cache、artifact 恢复、私有存储，或部署到 VPS/家用服务器定时运行。

## 本地定时运行

在 VPS 或本机上可以用 cron 调度：

```cron
0 6 * * * cd /path/to/rss-to-kindle && . .venv/bin/activate && python -m rss_to_kindle.cli run --config feeds.yml
```

本地/VPS 运行时，`.rss_to_kindle/state.db` 会自然持久化，因此去重和健康状态会连续保留。

## 测试

```bash
pytest
```

测试覆盖配置解析、重复 feed id、非法 status、URL tracking 参数清理、文章 ID 生成、SQLite 初始化、已发送文章去重、EPUB 生成、关键词过滤和 Atom 解析。

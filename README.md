# RSS to Kindle Daily Digest

通过 GitHub Actions 定时读取 `feeds.yml` 中的 RSS/Atom 订阅源，生成 EPUB 日报，并通过 Send to Kindle Email 发送到 Kindle。

## 部署到 GitHub Actions

### 1. 配置订阅源

复制示例配置并提交到仓库：

```bash
cp feeds.example.yml feeds.yml
```

编辑 `feeds.yml`：

```yaml
digest:
  max_epub_mb: 20
  max_images_per_article: 8
  max_image_budget_mb: 15
  max_single_image_mb: 3

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

常用规则：

- `status: active` 会参与每日投递。
- `status: testing` 默认不投递，只用于手动测试。
- `paused`、`archived`、`broken` 不参与投递。
- 文章必须有发布时间，并落在 `oldest_hours` 或 `digest.default_oldest_hours` 指定的时间窗口内。
- `include_keywords` 非空时，文章需要命中至少一个关键词；`exclude_keywords` 命中时会排除。

### 2. 配置 GitHub Secrets

在 GitHub 仓库中进入：

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

添加：

| Secret | 说明 |
| --- | --- |
| `SMTP_HOST` | SMTP 服务器地址 |
| `SMTP_PORT` | SMTP 端口，常见为 `587` |
| `SMTP_USER` | SMTP 登录账号 |
| `SMTP_PASS` | SMTP 登录密码或授权码 |
| `SMTP_USE_TLS` | 可选；为空或未配置时默认 `true` |
| `KINDLE_EMAIL` | Kindle 接收邮箱 |
| `SENDER_EMAIL` | 已在 Amazon 授权的发件邮箱 |

Amazon 侧需要把 `SENDER_EMAIL` 加入 Approved Personal Document Email List，否则 Kindle 可能拒收附件。

### 3. 启用每日任务

每日任务在 `.github/workflows/daily.yml` 中：

```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: "0 22 * * *"
```

GitHub Actions 使用 UTC 时间。当前配置 `22:00 UTC` 等于北京时间次日 `06:00`。

workflow 会执行：

- 安装 Python 依赖。
- 运行 `config-check --config feeds.yml`。
- 抓取新文章，生成 EPUB，并发送到 Kindle。
- 使用 GitHub Actions cache 恢复/保存 `.rss_to_kindle/state.db`，用于跨天去重和 feed 健康状态延续。
- 上传 `output/` 和 `.rss_to_kindle/state.db` 为 artifact，便于排查问题。

### 4. 手动运行

进入 GitHub 仓库：

`Actions` -> `RSS to Kindle Daily Digest` -> `Run workflow`

手动触发会使用当前分支上的 `feeds.yml` 和 Secrets。

## CI

`.github/workflows/ci.yml` 会在 push、pull request 和手动触发时运行：

```bash
pytest
python -m rss_to_kindle.cli config-check --config feeds.example.yml
```

CI 不会发送 Kindle 邮件。

## 输出与状态

- EPUB 文件会生成到 `output/`。
- 状态库路径为 `.rss_to_kindle/state.db`。
- 状态库记录 feed 健康状态、文章去重状态和投递日志。
- daily workflow 会通过 cache 跨次运行保留状态；artifact 主要用于下载检查。

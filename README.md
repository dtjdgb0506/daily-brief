# 推送系统

每天 07:30 发一封 HTML 邮件，内容是**前一天**的新闻（分析文章需要时间，早上正好能带上各方观点），
**5 条新闻 + 1 条「过去 7 天的重要后续」**。
每条是「陈述句标题 + 一句话 + 三条要点」，点标题进网页版看时间线和出处。

## 一条命令

```bash
python scripts/run_daily.py --dry-run     # 全流程跑一遍，不发信
python scripts/run_daily.py               # 真发（今天发过自动跳过）
python scripts/run_daily.py --auto-brief  # 没人写 brief 时用抽取式初稿兜底
python scripts/run_daily.py --force       # 强制重发
```

产物：`logs/<日期>.log`、`output/card-<日期>.html`、`site/<日期>/index.html`、`site/index.html`（往期）。

## 配置（改配置不用改代码）

| 文件 | 管什么 |
|---|---|
| `config/profile.toml` | 条数、类别权重、关注的人物/公司、`web_base`、聚类阈值、类别配额、时效半衰期、后续门槛 |
| `config/sources.toml` | 采集源（RSS），加一行 `[[feed]]` 就多一个源 |
| `config/entities.toml` | 实体别名（中/英/繁体），用于把不同媒体的同一事件合并 |
| `config/lexicon.toml` | 类别词表 + 标题规则词，决定一条新闻算科技/财经/政治 |
| `config/brief.json` | 当天的内容稿：标题/一句话/三条要点/时间线/出处（唯一人工步骤） |

## 流程

```
collect.py → cluster_events.py → （写 brief.json）→ build_card.py → send_html.py
```

- **collect**：RSS 出候选，按 URL 去重、48h 时间窗；默认只抓「前一天」的正文（`--day`），抓正文时提取发布日期并入库 `output/raw/<推送日>/`
- 前一天没有任何稿子时，自动回退到最近 24h，保证每天能发
- **cluster**：时间窗过滤（**只认页面上的发布日期**；没有日期、或最新一篇超过 48 小时的稿子直接丢）→ **软新闻过滤**（展会/活动/签约/旅游/券商研报 → 直接不要）→ TF-IDF + 实体聚类 → 类别按“证据多少”判 →
  评分 = 类别权重 × 稿量 × 时效衰减 × 来源多样性 × **源质量分层**（core 1.30 / tech 1.15 / analysis 1.10 / cn 0.95）× **硬新闻信号**（决策/人物/数字）→
  主推 = 5 个**新事件**（与过去 7 天重合的事件不进主推，单独作为第 6 条「后续」）；
  名额按当天实际有哪些类别动态分配（比例取自 [weights]；没有科技就多给政治/财经）。
  每天 5 条 = 5 个不同事件，不拆事件凑数；栏目名/视频页标题、软新闻都会过滤。
若某天全网不足 5 个不同事件，就发实际条数（宁少不重），页脚会写清本期几条。
- **build_card**：校验 + 渲染卡片/网页/往期索引；链接用 `web_base` 拼绝对地址
- **send_html**：Date/Message-ID、465 SSL 失败回退 587 STARTTLS、3 次重试、`--dry-run`

## 校验（发送前会拦）

- 标题是问句（`为什么……？`）→ 失败
- 有具体数字（`44%`、`75 亿`）但没出处 → 失败
- 标题重复、出处 URL 重复、缺 URL → 失败
- 条数不是配置的 5 条、标题超 40 字 → 告警

## 接大模型改写（可选）

`.env` 里加三行（OpenAI 兼容接口都行）：

```
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-chat
```

之后 `run_daily.py --auto-brief` 会自动：抽取初稿 → 模型改写 → 出卡。也可以手动：

```bash
python scripts/make_brief.py
python scripts/llm_polish.py            # 初稿 -> config/brief.json
python scripts/llm_polish.py --dry-run  # 只打印 prompt
```

没配 key 时自动跳过模型改写，用抽取式初稿兜底。

## 测试

```bash
python -m unittest discover -s tests -v
python scripts/cluster_events.py --fixture tests/fixture_articles.json \
    --history-dir tests/fixture_archive --date 2026-11-22 --no-archive
```

## 计划任务

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_task.ps1
```

## 详情页托管（国内可开、每件事一页）

每个事件发布成一个独立的 telegra.ph 页面（`scripts/publish_telegraph.py`，无需账号、无需 VPN，实测 1.1s），邮件里的「看详细」直接指过去；同时会在仓库 `briefs/<日期>/` 留一份 Markdown 备份。
GitHub 会把 Markdown 渲染成网页，`github.com` 国内可直连（不需要 VPN）。
样例：https://github.com/dtjdgb0506/daily-brief/blob/master/briefs/2026-11-22/p1.md

每页包含：时间线 → 多方分析（三种解读 / 看空看多 / 支持与质疑）→ 出处链接。
`make_brief.py` 还会从同一事件的多篇稿子里自动摘「各方观点」，保证自动生成的页面也有分析。

## 三个“没人/没服务器”时怎么办

**1. 采集源**：`config/sources.toml` 现在 **49 个源**（国际权威 25 / 国际科技 6 / 深度分析 9 / 国内主流 9）。
每个源带 `tier`，决定质量权重：core 1.30、tech 1.15、analysis 1.10、cn 0.95、local 0.55——
同样是新闻，国际权威源的事件排得更前。加源后先跑
`python scripts/check_sources.py --timeout 8 --workers 16` 看能不能出稿（或云端 `test-sources`）。
本机（国内）只能抓到约 22 个（境外源被墙），CI 跑在美国能抓全。
正文抓取有三道过滤：正文太短、标题词在正文里出现不到 35%、导航条污染（`| 北京 | 天津 |`）。

**2. brief 没人写**：`python scripts/make_brief.py` 会从 events.json + 原文里抽标题/导语/
三条要点/时间线/出处，生成 `config/brief.draft.json`；`run_daily.py --auto-brief` 会在
当天没稿时自动用它。质量比手写糙（导语是原文句子直搬），但能过全部校验、能发。

**3. 网页版地址 / 每天定时**：
- 本地预览：`powershell -ExecutionPolicy Bypass -File scripts/deploy_pages.ps1 -Local`（默认 8080 端口，手机同 WiFi 可看）
- 发布：先建空 GitHub 仓库，再
  `... -RemoteUrl https://github.com/<你>/<仓库>.git -WebBase https://<你>.github.io/<仓库>`
  （脚本会顺手把 `web_base` 写进 profile.toml；第一次还要在仓库 Settings → Pages 里把 Source 设成 `gh-pages`）
- 定时：`scripts/install_task.ps1`（注册每天 07:30 的 Windows 计划任务，跑 `run_daily.py`）

## 电脑关机也要推 → GitHub Actions

Windows 任务计划只能在本机开机/睡眠时跑。要关机也推，把整条流水线搬到 GitHub Actions：

1. 先登录并建仓库（一次性）：
   ```powershell
   gh auth login
   powershell -ExecutionPolicy Bypass -File scripts/setup_github.ps1
   ```
   脚本会：建仓库 → 推代码 → 把 `.env` 里的 MAIL_*/LLM_* 写进 Actions Secrets →
   打开 Pages → 把 `web_base` 改成 `https://<你>.github.io/<仓库>`。
2. 之后每天 **07:30（北京时间）** GitHub 自己跑（`.github/workflows/daily.yml`，cron `30 23 * * *`），
   出卡、发信、把网页版部署到 Pages。电脑关不关都不影响。
3. 第一次去仓库 Actions 页点一次 **Run workflow**，确认能发信。

也可以只要云端定时、不要 Pages：不跑 setup 脚本的 Pages 步骤，手动在仓库 Settings → Pages 里选 “GitHub Actions”。

## 内容原则：只发“分析过的人话”

- **评分**：事件里出现观点句（认为/指出/预计/analyst/warned…）→ 加分；官话套话多 → 降权
- **素材**：优先用带分析的原文（评论、深度稿），通稿靠后
- **改写**：提示词禁用一整套官话词（高质量发展/深入实施/动能向新/释放潜能/赋能/抓手…），
  强制结构：标题（陈述句）+ 发生了什么（带数字）+ 为什么重要 + 接下来看什么 + 普通人启示 + 值得琢磨的问题
- **重写**：成稿里官话命中 ≥4 次，自动让模型用“人话”重写一遍
- **校验**：单条官话 ≥3 处会在出卡时告警

## 已知边界

- 聚类是启发式的：同一天两件事同时涉及同一个人和同一家公司，会被并成一条。
- 选题质量靠三件事兜底：软新闻过滤（展会/活动/旅游/券商研报直接丢）、源分层（国际权威源权重更高）、
  硬新闻信号（有决策/人物/数字才加分）。仍可能漏掉或误杀，发现好新闻被过滤就把词从
  `scripts/cluster_events.py` 的 `SOFT_PATTERNS` 里挪出去。
- 自动初稿是抽取式的，导语/要点是原文句子直搬；接了大模型改写才算能日常直接发。
- 网页版支持暗黑模式（`prefers-color-scheme`）；邮件里仍是浅色，Outlook 兼容没专门处理。
- `web_base` 已指向腾讯云 CloudBase 静态托管；换域名后改 `profile.toml` 一处即可。
- 163 单日发送数有限，目前只发两个收件人。

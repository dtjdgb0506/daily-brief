# 每日情报 · 推送系统交接文档

> 交接对象：接手日常维护的人
> 生成时间：2026-11-22
> 代码仓库：https://github.com/dtjdgb0506/daily-brief （public）
> 线上地址：https://daily-brief-d4ggl7ududb678762-1538638844.tcloudbaseapp.com

## 最后核验（`python scripts/verify_handover.py` 自动生成，2026-11-22 18:42）

| 项目 | 实测 |
|---|---|
| 单元测试 | ✅ 12/12 全部通过 |
| 卡片占位符 | ✅ 没有 example.com 残留 |
| 线上站点 | HTTP 200 |
| 最近一次 Actions | 需本机网络能访问 api.github.com；以 Actions 页面为准 |
| 发信状态 | 本地还没有 `state/sent.json`（本地从未真发；云端是否发过看 Actions 记录） |
| 采集源 | 49 个源（core 25 / cn 9 / analysis 9 / tech 6） |
| 网页往期 | 2 期（2026-11-21 ~ 2026-11-22） |

---

## 一、这套系统是干什么的

每天早上 **07:30（北京时间）** 自动发一封邮件，内容是 **前一天的 5 条新闻 + 1 条「过去 7 天的重要后续」**。

每条新闻的结构：

- 一个陈述句标题（不是问句、不是官话）
- 一句话导语（发生了什么，带数字）
- 3 条要点：关键事实 / 为什么重要 / 接下来看什么
- 「各方观点」（从多篇稿子里自动摘的分析）
- 「值得琢磨的几个问题」（反常识的假设或悬念）
- 「普通人能学到什么 / 怎么用」
- 时间线 + 原文出处

点标题进的是**该事件独立的详情页**（不是把所有事塞在一个页面里）。

收件人：`dtjdgb0506@163.com`、`zhangyingzhuo@163.com`（一人一封，互不可见）。

---

## 二、整体架构

```
RSS 源（49 个：国际权威 25 / 国际科技 6 / 深度分析 9 / 国内主流 9）
        │  collect.py：按 URL 去重、48h 时间窗、只抓“前一天”的正文
        ▼
output/raw/<推送日>/   （每篇稿子存正文 + 发布日期 + 来源）
        │  cluster_events.py：按事件聚类、去重、打分、选 5 条 + 1 条后续
        │    · 软新闻过滤（展会/活动/签约/旅游/购物/娱乐/券商研报 → 直接不要）
        │    · 评分 = 重要性 × 兴趣：重要性看覆盖度/跨源/硬新闻信号；兴趣只做 ±20% 微调
        │    · 源质量系数只 ±12%（core 1.12 / cn 0.96），不再让“英文核心源”压过事件本身
        │    · 硬新闻信号（决策/人物/数字才算大事）
        ▼
output/events.json / events.md
        │  make_brief.py：抽取式初稿（标题/导语/要点/时间线/出处/观点句）
        │  llm_polish.py：DeepSeek 改写成人话（禁官话、补分析）
        ▼
config/brief.json → build_card.py：校验 + 渲染
        ├─ site/<日期>/index.html         网页版总览
        ├─ site/<日期>/p1.html … f1.html  每个事件独立页
        └─ output/card-<日期>.html        邮件卡片
        │
        ├─ deploy_cloudbase.py：部署到腾讯云 CloudBase
        └─ send_html.py：一人一封发给两个收件人
        ▼
run_daily.py 串起全流程（日志 / 幂等 / 失败回退 / 失败通知）
```

每天由 **GitHub Actions** 触发（`.github/workflows/daily.yml`），跑在 GitHub 的美国服务器上，所以**电脑关机也照发**，境外源也能抓。

---

## 三、目录与文件说明

### 配置（改配置不用改代码）

| 文件 | 作用 |
|---|---|
| `config/profile.toml` | 条数、类别权重、关注人物/公司、网页版地址、聚类阈值、时效半衰期、后续门槛 |
| `config/sources.toml` | 采集源（RSS），一行一个源，现 49 个；每个源带 `tier`（core/tech/analysis/cn/local），tier 决定质量权重 |
| `config/lexicon.toml` | 类别词表 + 标题规则 + 排除词（中英文），决定一条新闻算科技/财经/政治 |
| `config/entities.toml` | 实体别名（中英/繁体），用于把不同媒体的同一事件合并 |
| `config/brief.json` | 当天的人工稿（可选；没有就走自动稿） |
| `config/brief.draft.json` | 自动初稿（忽略，不入库） |
| `config/brief.auto.json` | 模型改写后的自动稿（忽略，不入库） |

### 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/collect.py` | 收 RSS → 候选清单 → 抓正文入库（`--day` 只抓某一天） |
| `scripts/fetch_article.py` | 单篇抓取；提取发布日期（meta/正文/URL 三层）；正文清洗 |
| `scripts/extract_main.py` | lxml 内容块打分，抽主内容（解决“只抓到导航条”） |
| `scripts/cluster_events.py` | 事件聚类、去重、打分、动态名额分配、7 天后续识别；软新闻过滤 + 源分层 + 硬新闻信号 |
| `scripts/check_sources.py` | 并发检查每个源能不能用、出多少条（`--timeout/--workers/--only`） |
| `scripts/selfcheck.py` | 发信前自检：结构/内容/串台/完整性/状态/环境；不过就不发 |
| `scripts/verify_handover.py` | 交接文档自检：单测/占位符/线上站点/Actions/发信状态/源数量 |
| `scripts/make_brief.py` | 抽取式初稿 |
| `scripts/llm_polish.py` | 调 DeepSeek 改写成“人话 + 分析”，禁官话，官话过多自动重写 |
| `scripts/build_card.py` | 校验（问句标题/重复 URL/数字无出处/官话）+ 渲染卡片与网页 |
| `scripts/deploy_cloudbase.py` | 部署 `site/` 到腾讯云 CloudBase |
| `scripts/send_html.py` | 发信：一人一封、Date/Message-ID、重试、465/587 双通道 |
| `scripts/send_from_state.py` | 准点发信：读 `state/cards/<日期>.html` 直接发，不抓取不生成（07:30 用） |
| `scripts/notify_failure.py` | 流水线失败时发“今天没发成”提醒 |
| `scripts/run_daily.py` | 主流程编排（日志/幂等/前一天/失败回退） |
| `scripts/jargon.py` | 官话检测词表 |
| `scripts/print_brief.py` | 打印一期 brief，便于检查 |
| `scripts/install_task.ps1` | （备用）注册本机 Windows 计划任务 |
| `scripts/deploy_cloudbase.ps1`、`deploy_pages.ps1` | （备用）PowerShell 部署脚本 |

### 测试与自检

| 文件 | 作用 |
|---|---|
| `tests/test_pipeline.py` | 单元测试：RSS 解析、卡片校验、聚类 5+1、自动初稿、LLM 合并 |
| `tests/fixture_articles.json` / `fixture_archive/` | 聚类回归样例 |
| `.github/workflows/test-sources.yml` | 手动触发：检查每个源能拿多少条（不发信） |
| `.github/workflows/test-pipeline.yml` | 手动触发：按昨日跑一遍全流程，只打印结果（不发信） |

---

## 四、每天发生什么（自动流程，两段式）

1. **06:50（北京时间）** GitHub Actions 触发 `prepare.yml`
2. 计算内容日 = 昨天（`--same-day` 才用当天）
3. `collect.py --fetch 150 --day 昨天` 抓昨天的正文（每个源最多取 4 篇最新的，49 个源都能轮到）
4. `cluster_events.py --content-day 昨天`：只要昨天发布、48 小时内、有日期的稿子；**先过滤软新闻**（展会/活动/签约/旅游/购物/娱乐/券商研报），再按“重要性 × 兴趣”打分；**主推 5 条 = 5 个新事件**，与过去 7 天重合的事件单独作为第 6 条「后续」
5. 没有人工稿时：`make_brief.py` → `llm_polish.py` 生成自动稿
6. `build_card.py` 校验并渲染；`deploy_cloudbase.py` 部署网页版；卡片和主题存进 `state/cards/<日期>.html`（**这一轮不发信**）
7. **07:30** 触发 `daily.yml`：只跑 `send_from_state.py`，读 `state/cards/` 直接发信（10~20 秒）
8. 06:50 那轮没准备出来时，07:30 会回退成“现跑一遍全流程”
9. 任一步失败：`notify_failure.py` 发失败提醒

**幂等**：`state/sent.json` 记录当天是否已发；这个文件**已入仓库**，CI 每次发完会自动 commit + push（`daily.yml` 最后一步），所以同一天手动重跑不会重复发（`--force` 可强制）。邮件 Message-ID 也用「主题+收件人」做种子，即使真发两遍，客户端也会折叠。
**日志**：`logs/<推送日>.log`，云端可在 Actions 运行记录里看。

---

## 五、操作手册

### 本地手动跑一次（不发信）

```bash
python scripts/run_daily.py --dry-run
```

### 本地跑并真发

```bash
python scripts/run_daily.py
```

### 只跑某一天（比如补发）

```bash
python scripts/collect.py --fetch 150 --day 2026-11-21
python scripts/cluster_events.py --content-day 2026-11-21
python scripts/make_brief.py --date 2026-11-21 --out config/brief.draft.json
python scripts/llm_polish.py --in config/brief.draft.json --out config/brief.json
python scripts/build_card.py --brief config/brief.json --date 2026-11-21
python scripts/deploy_cloudbase.py
python scripts/send_html.py --subject "每日情报 11·21｜..." --html output/card-2026-11-21.html
```

### 云端手动触发一次

GitHub → Actions →「每日情报」→ Run workflow。

### 外部定时触发（替代 GitHub schedule，准点用）

GitHub 自带的 schedule 实测会延迟几小时甚至整轮丢失。用外部定时服务（cron-job.org 免费），到点 POST 一次：

```bash
# 06:50 准备
curl -X POST https://api.github.com/repos/dtjdgb0506/daily-brief/dispatches \
  -H "Authorization: Bearer <细粒度 PAT，权限 Actions: write>" \
  -H "Accept: application/vnd.github+json" \
  -d '{"event_type":"prepare"}'

# 07:30 发信
curl -X POST https://api.github.com/repos/dtjdgb0506/daily-brief/dispatches \
  -H "Authorization: Bearer <同一 PAT>" \
  -H "Accept: application/vnd.github+json" \
  -d '{"event_type":"send"}'
```

两个 workflow 都已加 `repository_dispatch` 触发（`prepare` / `send`）。cron-job.org 里建两个任务、时区选 Asia/Shanghai、开“POST + 自定义头 + JSON body”即可。重复触发不会重复发信：`state/sent.json` 幂等兜底。

### 改内容口味

- 想多推某类：改 `profile.toml` 的 `[weights]`
- 想加/换源：改 `config/sources.toml`，先跑 `test-sources` workflow 看能不能出稿
- 想改“人话”规则：改 `llm_polish.py` 里的 `SYSTEM` 提示词与 `jargon.py` 的禁用词
- 想改选题逻辑：改 `cluster_events.py` 的评分与 `pick_balanced` 名额分配

### 每天要看什么

1. 邮件是否收到两份（两个邮箱各一封）
2. 点「看详细」能否打开（会有 3 秒腾讯云测试域名提示页）
3. Actions 是否绿色；红色点进去看最后几十行报错

---

## 六、凭据与账号（不入库）

所有凭据只在两处：

1. **本机** `D:\DSH\推送系统\.env`（已被 `.gitignore` 忽略）
2. **GitHub Secrets**（仓库 Settings → Secrets and variables → Actions）

| 名称 | 用途 |
|---|---|
| `MAIL_USER` / `MAIL_PASS` / `MAIL_TO` | 163 邮箱账号、SMTP 授权码、收件人（逗号分隔） |
| `LLM_API_KEY` | DeepSeek API Key（OpenAI 兼容接口） |
| `TCB_ENV_ID` / `TCB_SECRET_ID` / `TCB_SECRET_KEY` | 腾讯云 CloudBase 环境与 API 密钥 |

其他账号信息：

- 腾讯云 CloudBase 环境：`daily-brief-d4ggl7ududb678762`（**免费体验版，2027-05-22 到期，每 6 个月要续期一次**）
- GitHub 仓库：`dtjdgb0506/daily-brief`（public）
- telegra.ph 的 `TELEGRAPH_TOKEN` 已停用（境外站要翻墙，不再使用）

**安全建议**：`TCB_SECRET_ID/KEY` 目前是主账号密钥，权限很大，建议后续换成子账号最小权限密钥并定期轮换。

---

## 七、已知问题与限制

1. **腾讯云测试域名有 3 秒中间页**（“页面访问提示”）。去掉它只能绑自定义域名，需要域名 + ICP 备案（1–3 周、约 ¥150/年）；当前决定先不管。
2. **免费环境 2027-05-22 到期**，需每 6 个月手动续期，否则环境回收、所有链接失效。
3. **网页版归档索引只列最近两期**：CI 每次构建的 `site/` 只有当天，根页“往期”靠本地 build 累积；旧的详情页还在云上，但入口会缺。
4. **模型可能把数字写错**：提示词要求数字来自素材，但只做了“有出处”的校验，没有逐条核对。
5. **境外源依赖 GitHub 出口**：本机（国内）直连会超时，本地跑只能抓到约 22 个源（多为国内 + 部分国际站）。CI 上能抓 49 个。
6. **软新闻过滤会误杀**：行业峰会、产品发布会、政策吹风会这类标题可能被当成“活动稿”过滤掉；如果发现好新闻被漏，把词加进 `scripts/cluster_events.py` 的 `SOFT_PATTERNS` 例外或调整。
7. **部分源不可用**：Reuters、Nikkei、Foreign Affairs、Politico 等有反爬/付费墙（Politico、Nikkei、Foreign Affairs 已重新加入，云端能不能出稿以 `check_sources.py` 为准）；Nature 的 RSS 有时返回空。
8. **GitHub schedule 不可靠（实测）**：2026-11-23 的 06:50 准备任务被延迟到 **11:11** 才跑（晚了 4 小时 21 分），07:30 的发送任务当天**完全没触发**。两段式只能缓解“抓取慢”，挡不住 GitHub 丢跑。要准点必须用外部定时触发 `repository_dispatch`（见“五、操作手册 → 外部定时触发”），或换境外 VPS 跑 cron。
9. **某天事件不足 5 条时只发实际条数**（宁少不重，不拆事件凑数）。
10. **每天会多一个机器 commit**：CI 发完信把 `state/sent.json` 提交回仓库（`[skip ci]`），这是防重复发的代价。
11. 邮件里的详情链接会先经过腾讯云中间页（见第 1 条）。

---

## 八、常见故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 没收到邮件 | Actions 失败 / SMTP 拒登 / 收件人被改 | 看 Actions 日志；`test_smtp.py` 自检；检查 Secrets 的 `MAIL_TO` |
| 邮件到了但链接打不开 | CloudBase 部署失败 / 环境到期 | 看日志「部署网页版」段；`python scripts/deploy_cloudbase.py` 手动部署 |
| 内容都是旧闻 | 发布日期抓取失败 / content-day 参数不对 | 看 `events.md` 里素材日期；跑 `test-pipeline` 自检 |
| 5 条内容雷同 | 聚类阈值太低（误合并） | 调 `profile.toml [cluster].threshold` 或检查正文清洗 |
| 内容官话多 | 模型没按人话规则写 / 素材太差 | 看 `llm_polish.py` 提示词；换分析源（Foreign Policy 等） |
| 源都抓不到 | 网络 / RSS 失效 | 跑 `test-sources` workflow 查看每个源计数 |

---

## 九、常用命令速查

```bash
# 全流程（发信）
python scripts/run_daily.py

# 全流程（不发信）
python scripts/run_daily.py --dry-run

# 只检查源能不能用
python scripts/collect.py --dry-run

# 并发检查每个源的可用性（比上面快，且逐个报数量）
python scripts/check_sources.py --timeout 8 --workers 16

# 交接文档自检（把文档和实际状态对一遍）
python scripts/verify_handover.py

# 按昨天跑但只到校验
python scripts/run_daily.py --dry-run --auto-brief

# 单测
python -m unittest discover -s tests -v

# 部署网页版
python scripts/deploy_cloudbase.py

# 打印某期 brief
python scripts/print_brief.py config/brief.json
```

---

## 十、下一步建议（按优先级）

1. **域名 + 备案**：去掉中间页，链接体验最好（一次性成本约 ¥150/年）
2. **归档索引**：把历史日期写进一个小 JSON 并每天提交，根页就能列出全部往期
3. **数字核对**：模型输出的数字与素材做二次比对，不符就回退
4. **换子账号密钥**：降低腾讯云主账号密钥风险
5. **继续扩国际源**：已从 25 个扩到 49 个（国际权威 25 / 国际科技 6 / 深度分析 9）；下一步补财新、彭博、路透镜像等，加源后先跑 `check_sources.py` 或云端 `test-sources` 看能不能出稿
6. **把 `brief.json` 的人工稿流程固化**：目前自动稿已可用，但有重大事件时手写一版质量更高

---

## 十一、本次交接的时间线

- 2026-11-22：从零搭好采集/聚类/出卡/发信全流程
- 同天：接入腾讯云 CloudBase 托管、GitHub Actions 云端定时、DeepSeek 改写
- 同天：修复旧闻冒充今天、无关新闻误合并、官话过多等问题
- 同天：改为「推前一天新闻」+「每条独立详情页」+「分析/问题/启示」结构
- 同天（晚）：修复幂等（状态入仓 + CI 自动提交 + Message-ID 去重）、5+1 结构、选题质量（软新闻过滤/源分层/硬新闻信号/兜底类别降权）、国际源扩到 49 个
- 当前状态：**已上线，每天 07:30 自动推送，双收件人**

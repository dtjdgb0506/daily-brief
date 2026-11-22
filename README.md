# 推送系统

每天 07:30 发一封 HTML 邮件：**5 条新闻 + 1 条「过去 7 天的重要后续」**。
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

- **collect**：3 个 RSS 源出候选（今天 139 条），按 URL 去重、48h 时间窗，抓正文按日期入库 `output/raw/<日期>/`
- **cluster**：时间窗过滤 → TF-IDF + 实体聚类 → 类别按“证据多少”判（不是按权重，否则全归科技）→
  评分 = 类别权重 × 稿量 × 时效衰减 × 来源多样性 → 类别配额（同类最多 3 条，但换来的分差超 30% 就不换）→
  跟过去 7 天归档比对，挑一条「没进前 5 但确实是旧事件新进展」的当第六点
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

## 三个“没人/没服务器”时怎么办

**1. 采集源**：`config/sources.toml` 现在 8 个源（IT之家 / Solidot / 华尔街见闻 / 界面新闻 /
新华网-时政 / 中国新闻网 / 联合国新闻 / …）。正文抓取会做三道过滤：正文太短、标题词在正文里
出现不到 35%、导航条污染（`| 北京 | 天津 |`）——都已经在 `cluster_events.py` 的 `load_articles` 里。
目前政治源的问题是**网页正文抓不到**（新华网点进去常是视频页、中新网只剩导航），
下一步要么给这两个站写专用抽取规则，要么换 API/付费源。

**2. brief 没人写**：`python scripts/make_brief.py` 会从 events.json + 原文里抽标题/导语/
三条要点/时间线/出处，生成 `config/brief.draft.json`；`run_daily.py --auto-brief` 会在
当天没稿时自动用它。质量比手写糙（导语是原文句子直搬），但能过全部校验、能发。

**3. 网页版地址 / 每天定时**：
- 本地预览：`powershell -ExecutionPolicy Bypass -File scripts/deploy_pages.ps1 -Local`（默认 8080 端口，手机同 WiFi 可看）
- 发布：先建空 GitHub 仓库，再
  `... -RemoteUrl https://github.com/<你>/<仓库>.git -WebBase https://<你>.github.io/<仓库>`
  （脚本会顺手把 `web_base` 写进 profile.toml；第一次还要在仓库 Settings → Pages 里把 Source 设成 `gh-pages`）
- 定时：`scripts/install_task.ps1`（注册每天 07:30 的 Windows 计划任务，跑 `run_daily.py`）

## 已知边界

- 聚类是启发式的：同一天两件事同时涉及同一个人和同一家公司，会被并成一条。
- 采集源仍然偏科技：能用的政治源正文抓不干净（见上），所以今天 top5 仍可能全 tech；
  类别配额只在“有别的类可用且分差不超过 30%”时才换，不会为了多样性塞垃圾。
- 自动初稿是抽取式的，导语/要点是原文句子直搬；接了大模型改写才算能日常直接发。
- 网页版支持暗黑模式（`prefers-color-scheme`）；邮件里仍是浅色，Outlook 兼容没专门处理。
- `web_base` 现在是占位符，正式发信前改成你自己的托管地址；`site/` 整个目录可直接丢 GitHub Pages。
- 暗黑模式、Outlook 兼容还没做；163 单日发送数有限，目前只发自己。

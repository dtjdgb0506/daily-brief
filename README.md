# 推送系统

每天 07:30 发一封 HTML 邮件：**5 条新闻 + 1 条「过去 7 天的重要后续」**。
每条是「陈述句标题 + 一句话 + 三条要点」，点标题进网页版看时间线和出处。

## 一条命令

```bash
python scripts/run_daily.py --dry-run     # 全流程跑一遍，不发信
python scripts/run_daily.py               # 真发（今天发过自动跳过）
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

## 已知边界

- 聚类是启发式的：同一天两件事同时涉及同一个人和同一家公司，会被并成一条。
- 采集源偏科技（新浪/联合早报/BBC 的 RSS 目前都返回空或非 XML），政治/国际源待补；
  源偏科时前 5 条会全是同一类，配额只能在有别的类可用时才起作用。
- `web_base` 现在是占位符，正式发信前改成你自己的托管地址；`site/` 整个目录可直接丢 GitHub Pages。
- 暗黑模式、Outlook 兼容还没做；163 单日发送数有限，目前只发自己。

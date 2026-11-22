# 推送系统

每天 07:30 发一封 HTML 邮件：5 条新闻 + 1 条「过去 7 天的重要后续」。
每条是「陈述句标题 + 一句话 + 三条要点」，点标题进网页版看时间线和出处。

## 一条命令

```bash
python scripts/run_daily.py --dry-run     # 全流程跑一遍，不发信
python scripts/run_daily.py               # 真发（今天发过会自动跳过）
python scripts/run_daily.py --force       # 强制重发
```

产物：`logs/<日期>.log`、`output/card-<日期>.html`、`site/<日期>/index.html`。

## 目录

```
config/profile.toml   兴趣档：条数/权重/关注对象/网页版地址
config/sources.toml   采集源（RSS），想加源就加一个 [[feed]]
config/brief.json     当天的内容稿：标题/一句话/三条要点/时间线/出处（这一步人工或模型写）
scripts/collect.py         ① 从 RSS 收候选，抓正文入库 output/raw/<日期>/
scripts/fetch_article.py   单篇抓取 + 提取发布时间（collect 复用它）
scripts/cluster_events.py  ② 按事件聚类、去重、选出 5 条 + 1 条后续
scripts/build_card.py      ③ 把 brief.json 渲染成邮件卡片 + 网页版，并做校验
scripts/send_html.py       ④ 发信
scripts/run_daily.py       把 ①→④ 串起来，带日志、幂等、失败退出
scripts/install_task.ps1   注册 Windows 计划任务（每天 07:30）
scripts/test_smtp.py       发信通道自检
tests/                     聚类回归样例（5 个互不相关事件 + 1 条历史后续）
```

## 流程

```
collect.py → cluster_events.py → （写 brief.json）→ build_card.py → send_html.py
```

`cluster_events.py` 做三件事：

1. **按时间窗**只取最近 N 小时的稿件（默认 24h；老稿件没有发布时间时用文件时间兜底）
2. **按事件聚类**：TF-IDF 余弦 + 实体规则（同一人物 + 同一公司直接并；跨中英文/繁体）
3. **挑第六点**：把今天的事件跟 `output/archive/` 里过去 7 天的事件比对，找出「没挤进前 5、
   但确实是旧事件新进展」的那条，按 类别权重/稿量/关键节点/匹配强度 打分，过线才放。

## 校验（build_card.py）

发送前会拦：

- 标题是问句（`为什么……？`）→ 直接失败
- 出处 URL 重复、缺 URL
- 条数不是配置的 5 条 → 告警
- event_id 在 events.json 里找不到 → 告警（人工补稿正常）

## 每天要做的那一步

`brief.json` 是目前唯一需要人（或模型）动手的部分：读完 `output/events.md`，把当天 5 个事件
写成标题/一句话/三条要点/时间线/出处。写完 `run_daily.py` 一条命令出卡发信。

## 邮件链接

`config/profile.toml` 的 `[delivery].web_base` 是网页版根地址，卡片里的链接会拼成
`{web_base}/{日期}/#pN`。`site/<日期>/` 整个目录可直接丢到 GitHub Pages / Cloudflare Pages。
本地预览用 `site/<日期>/card-preview.html`（相对链接）。

## 计划任务

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_task.ps1
```

## 已知边界

- 聚类是启发式的：同一天两件事同时涉及同一个人和同一家公司，会被并成一条（写 brief 时可以拆）。
- 后续判定用的是关键词+实体，阈值 `importance >= 0.4`；历史数据攒起来后可以再校准。
- 163 邮箱单日发送数有限制，目前只发给自己；发多人要补退订/频控。

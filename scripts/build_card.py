"""把 config/brief.json 渲染成邮件卡片 + 网页版，并在渲染前做校验。

用法:
  python scripts/build_card.py                       # 读 config/brief.json
  python scripts/build_card.py --brief other.json --date 2026-11-22
  python scripts/build_card.py --dry-run             # 只校验不写文件

产物:
  site/<date>/index.html   网页版（邮件标题链到这里）
  site/<date>/card.html    本地预览用卡片（相对链接）
  output/card-<date>.html  发送用卡片（链接拼成绝对地址，WEB_BASE 配在 profile.toml）

校验（不通过直接退出码 2）:
  - 标题不能是问句（你明确不要“为什么……”这类标题）
  - 5 条新闻 + 最多 1 条后续；缺条只告警
  - 同一 URL 不能出现两次
  - event_id 在 output/events.json 里找不到 → 告警（可能是人工补的稿）
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TAG_CLASS = {"tech": "tech", "fin": "fin", "pol": "pol", "politics": "pol", "finance": "fin"}
BAD_TITLE_END = ("？", "?")

CARD_HEAD = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#eef1f5;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef1f5;">
<tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;background:#ffffff;border-radius:14px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
<tr><td style="background:#0b1220;padding:22px 26px 20px;">
  <div style="font-size:11px;letter-spacing:3px;color:#7c8aa5;font-weight:700;">{kicker}</div>
  <div style="font-size:13px;color:#93a3bd;margin-top:7px;">{meta}</div>
</td></tr>
"""

CARD_FOOT = """<tr><td style="padding:28px 26px 26px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e2e8f0;">
  <tr><td style="padding-top:14px;font-size:12px;line-height:1.9;color:#94a3b8;">
    每天 07:30 发出 · 5 条新闻 + 1 条后续 · 点标题进网页版看完整时间线和出处。<br>
    生成于 {gen} · 校验通过。
  </td></tr>
  </table>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""

WEB_HEAD = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>每日情报 · 网页版｜{date}</title>
<style>
  :root{{--ink:#0f172a;--body:#1e293b;--mute:#64748b;--line:#e8edf3;--blue:#1d4ed8}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:#eef1f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:var(--body);-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:760px;margin:0 auto;padding:20px 10px 60px}}
  .head{{background:#0b1220;border-radius:14px 14px 0 0;padding:22px 26px;color:#93a3bd}}
  .head .kicker{{font-size:11px;letter-spacing:3px;font-weight:700;color:#7c8aa5}}
  .head .date{{font-size:13px;margin-top:7px}}
  .head h1{{margin:10px 0 0;font-size:22px;color:#fff;font-weight:700;letter-spacing:.5px}}
  .card{{background:#fff;border-radius:0 0 14px 14px;padding:4px 26px 30px}}
  article{{padding:26px 0 6px;border-bottom:1px solid var(--line)}}
  article:last-of-type{{border-bottom:0}}
  .tag{{display:inline-block;font-size:11px;line-height:1;border-radius:999px;padding:4px 10px;font-weight:700}}
  .tag.tech{{color:#1e40af;background:#dbeafe}}
  .tag.fin{{color:#92400e;background:#fef3c7}}
  .tag.pol{{color:#5b21b6;background:#ede9fe}}
  article h2{{margin:12px 0 10px;font-size:24px;line-height:1.4;color:var(--ink)}}
  .lede{{font-size:16.5px;line-height:1.8;font-weight:600;color:var(--ink);margin:0 0 18px}}
  .sec{{margin:20px 0 0}}
  .sec h3{{font-size:13px;letter-spacing:1px;color:var(--mute);font-weight:700;margin:0 0 10px;text-transform:uppercase}}
  .tl{{list-style:none;margin:0;padding:0 0 0 14px;border-left:2px solid var(--line)}}
  .tl li{{position:relative;padding:0 0 12px 14px;font-size:14.5px;line-height:1.75}}
  .tl li::before{{content:'';position:absolute;left:-21px;top:8px;width:8px;height:8px;border-radius:50%;background:#cbd5e1;border:2px solid #fff}}
  .tl b{{color:var(--ink);font-variant-numeric:tabular-nums;margin-right:6px}}
  .box{{background:#f8fafc;border-radius:10px;padding:14px 16px;font-size:14.5px;line-height:1.8}}
  .box.warn{{background:#fff7ed;color:#9a3412}}
  .box p{{margin:0 0 8px}}.box p:last-child{{margin:0}}
  .src{{font-size:13px;line-height:1.9;color:var(--mute)}}
  .src a{{color:var(--blue);text-decoration:none;border-bottom:1px solid #bfdbfe}}
  .top{{display:block;margin-top:14px;font-size:12.5px;color:#94a3b8;text-decoration:none}}
  .foot{{margin-top:26px;padding-top:16px;border-top:1px solid var(--line);font-size:12.5px;line-height:1.9;color:#94a3b8}}
  .history{{display:inline-block;font-size:12.5px;color:#9a3412;background:#fff7ed;border-radius:8px;padding:6px 10px;margin:0 0 14px}}
  @media(max-width:480px){{article h2{{font-size:21px}}.lede{{font-size:15.5px}}.card{{padding-left:18px;padding-right:18px}}}}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div class="kicker">DAILY BRIEF · WEB</div>
    <div class="date">{date} · 5 条新闻 + 1 条后续</div>
    <h1>{doc_title}</h1>
  </div>
  <div class="card">
"""

WEB_FOOT = """  <div class="foot">
    每日情报 · 网页版 · {date} · 5 条新闻 + 1 条后续 · 所有事实与数字均来自上方链接原文。<br>
    生成于 {gen}
  </div>
  </div>
</div>
</body>
</html>
"""


def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_profile() -> dict:
    path = ROOT / "config" / "profile.toml"
    if not path.exists():
        return {}
    import tomllib
    with path.open("rb") as f:
        return tomllib.load(f)


def validate(brief: dict, events: dict | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []

    items = brief.get("items", [])
    items_cfg = load_profile().get("delivery", {}).get("items", 5)
    if len(items) != items_cfg:
        warns.append(f"新闻条数 {len(items)} ≠ 配置的 {items_cfg} 条")
    if len(items) < 3:
        errors.append(f"新闻只有 {len(items)} 条，太少")

    seen_urls: set[str] = set()
    event_ids = set()
    if events:
        event_ids = {e["id"] for e in events.get("events", [])}

    for i, it in enumerate(items, 1):
        title = it.get("title", "")
        if not title:
            errors.append(f"第 {i} 条没有标题")
        elif title.strip().endswith(BAD_TITLE_END):
            errors.append(f"第 {i} 条标题是问句（{title}），要改成概括事件的陈述句")
        if len(title) > 40:
            warns.append(f"第 {i} 条标题 {len(title)} 字，邮件里会折行，建议压到 30 字内")
        if not it.get("lede"):
            errors.append(f"第 {i} 条缺 lede")
        if len(it.get("points", [])) != 3:
            warns.append(f"第 {i} 条要点 {len(it.get('points', []))} 条（建议正好 3 条）")
        eid = it.get("event_id")
        if eid and event_ids and eid not in event_ids:
            warns.append(f"第 {i} 条 event_id={eid} 不在 events.json 里（人工补的稿？）")

        for src in (it.get("web") or {}).get("sources", []):
            url = src.get("url", "")
            if not url:
                errors.append(f"第 {i} 条有出处缺 URL")
            elif url in seen_urls:
                errors.append(f"出处重复：{url}")
            seen_urls.add(url)
        if not (it.get("web") or {}).get("sources"):
            warns.append(f"第 {i} 条没有出处")

    fu = brief.get("followup")
    if fu:
        if not fu.get("title") or not fu.get("lede"):
            errors.append("后续条目缺 title/lede")
        if fu.get("title", "").strip().endswith(BAD_TITLE_END):
            errors.append("后续标题是问句，要改成陈述句")
    return errors, warns


def render_points(points: list, font_size: float = 14.5, border: str = "#e8edf3") -> str:
    rows = []
    for label, text in points:
        rows.append(
            f'<tr><td style="padding:9px 0;border-top:1px solid {border};font-size:{font_size}px;line-height:1.8;color:#1e293b;">'
            f'<strong style="color:#0f172a;">{esc(label)}</strong>{esc(text)}</td></tr>'
        )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table>'


def render_card(brief: dict, link_prefix: str) -> str:
    out = [CARD_HEAD.format(
        title=esc(brief.get("doc_title", "每日情报")),
        preheader=esc(brief.get("preheader", "")),
        kicker=esc(brief.get("kicker", "DAILY BRIEF")),
        meta=esc(brief.get("meta", "")),
    )]
    items = brief.get("items", [])
    fg_map = {"tech": "#1e40af", "fin": "#92400e", "pol": "#5b21b6"}
    bg_map = {"tech": "#dbeafe", "fin": "#fef3c7", "pol": "#ede9fe"}
    for idx, it in enumerate(items, 1):
        tag_kind = TAG_CLASS.get(it.get("tag_kind", "tech"), "tech")
        fg, bg = fg_map[tag_kind], bg_map[tag_kind]
        border = "border-top:1px solid #e8edf3;" if idx > 1 else ""
        pad = "24px 26px 0" if idx > 1 else "26px 26px 0"
        h = "h1" if idx == 1 else "h2"
        out.append(f'<tr><td style="padding:{pad};{border}">')
        out.append(
            f'<span style="display:inline-block;font-size:11px;line-height:1;color:{fg};background:{bg};'
            f'border-radius:999px;padding:4px 10px;font-weight:700;">{esc(it.get("tag", ""))}</span>'
        )
        fs = 24 if idx == 1 else 21
        out.append(
            f'<{h} style="margin:12px 0 10px;font-size:{fs}px;line-height:1.4;color:#0f172a;font-weight:700;">'
            f'<a href="{link_prefix}#p{idx}" style="color:#0f172a;text-decoration:none;">{esc(it.get("title", ""))}</a></{h}>'
        )
        out.append(
            f'<p style="margin:0 0 8px;font-size:15px;line-height:1.8;color:#1e293b;font-weight:600;">'
            f'{esc(it.get("lede", ""))}</p>'
        )
        out.append(render_points(it.get("points", []), 14))
        note = it.get("note")
        if note:
            out.append(
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                'style="background:#fff7ed;border-radius:10px;margin-top:14px;"><tr>'
                f'<td style="padding:13px 15px;font-size:13.5px;line-height:1.8;color:#9a3412;">'
                f'<strong>{esc(note.get("title", ""))}</strong>{esc(note.get("text", ""))}</td></tr></table>'
            )
        watch = it.get("watch")
        if watch:
            rows = "".join(
                f'<tr><td style="padding:3px 0;">{esc(x)}</td></tr>' for x in watch.get("items", [])
            )
            out.append(
                f'<h2 style="margin:16px 0 6px;font-size:14px;color:#0f172a;font-weight:700;">{esc(watch.get("title", ""))}</h2>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="font-size:13.5px;line-height:1.8;color:#334155;">{rows}</table>'
            )
        hint = it.get("detail_hint", "")
        out.append(
            f'<a href="{link_prefix}#p{idx}" style="display:inline-block;margin-top:10px;font-size:13.5px;'
            f'font-weight:700;color:#1d4ed8;text-decoration:none;">看详细：{esc(hint)} →</a>'
        )
        out.append("</td></tr>")

    fu = brief.get("followup")
    if fu:
        out.append(
            '<tr><td style="padding:28px 26px 0;border-top:1px solid #e8edf3;">'
            '<div style="font-size:11px;letter-spacing:3px;color:#9a3412;font-weight:700;">'
            f'{esc(fu.get("section_title", "过去 7 天的重要后续"))}</div>'
            '</td></tr>'
        )
        out.append(
            '<tr><td style="padding:12px 26px 0;">'
            f'<span style="display:inline-block;font-size:11px;line-height:1;color:#9a3412;background:#ffedd5;'
            f'border-radius:999px;padding:4px 10px;font-weight:700;">{esc(fu.get("tag", "后续"))}</span>'
            f'<h2 style="margin:12px 0 10px;font-size:21px;line-height:1.45;color:#0f172a;font-weight:700;">'
            f'<a href="{link_prefix}#f1" style="color:#0f172a;text-decoration:none;">{esc(fu.get("title", ""))}</a></h2>'
            f'<p style="margin:0 0 8px;font-size:15px;line-height:1.8;color:#1e293b;font-weight:600;">{esc(fu.get("lede", ""))}</p>'
            + render_points(fu.get("points", []), 14)
        )
        hist = fu.get("history")
        if hist:
            out.append(
                f'<div style="margin-top:12px;font-size:12.5px;color:#9a3412;">'
                f'来自 {esc(hist.get("date", ""))} 的事件：{esc(hist.get("title", ""))}</div>'
            )
        out.append(
            f'<a href="{link_prefix}#f1" style="display:inline-block;margin-top:10px;font-size:13.5px;'
            f'font-weight:700;color:#1d4ed8;text-decoration:none;">看详细：{esc(fu.get("detail_hint", ""))} →</a>'
        )
        out.append("</td></tr>")

    out.append(CARD_FOOT.format(gen=datetime.now().strftime("%Y-%m-%d %H:%M")))
    return "\n".join(out)


def render_web(brief: dict) -> str:
    parts = [WEB_HEAD.format(
        date=esc(brief.get("date", "")),
        doc_title=esc(brief.get("doc_title", "")),
    )]
    for idx, it in enumerate(brief.get("items", []), 1):
        tag_kind = TAG_CLASS.get(it.get("tag_kind", "tech"), "tech")
        w = it.get("web", {})
        parts.append(f'<article id="p{idx}">')
        parts.append(f'<span class="tag {tag_kind}">{esc(it.get("tag", ""))}</span>')
        parts.append(f'<h2>{esc(it.get("title", ""))}</h2>')
        parts.append(f'<p class="lede">{esc(it.get("lede", ""))}</p>')
        if w.get("timeline"):
            parts.append('<div class="sec"><h3>时间线</h3><ul class="tl">')
            for t, txt in w["timeline"]:
                parts.append(f"<li><b>{esc(t)}</b>{esc(txt)}</li>")
            parts.append("</ul></div>")
        for blk in w.get("blocks", []):
            cls = "box warn" if blk.get("type") == "warn" else "box"
            lines = "".join(f"<p>{esc(x)}</p>" for x in blk.get("lines", []))
            parts.append(f'<div class="sec"><h3>{esc(blk.get("title", ""))}</h3><div class="{cls}">{lines}</div></div>')
        if w.get("sources"):
            links = " · ".join(
                f'<a href="{esc(s.get("url", ""))}" target="_blank" rel="noopener">{esc(s.get("label", ""))}</a>'
                for s in w["sources"]
            )
            parts.append(f'<div class="sec"><h3>出处</h3><p class="src">{links}</p></div>')
        parts.append(f'<a class="top" href="#p{idx}">↑ 回到本条</a></article>')

    fu = brief.get("followup")
    if fu:
        w = fu.get("web", {})
        parts.append('<article id="f1">')
        hist = fu.get("history") or {}
        if hist:
            parts.append(f'<div class="history">后续自 {esc(hist.get("date", ""))}：{esc(hist.get("title", ""))}</div><br>')
        parts.append(f'<span class="tag fin">{esc(fu.get("tag", "后续"))}</span>')
        parts.append(f'<h2>{esc(fu.get("title", ""))}</h2>')
        parts.append(f'<p class="lede">{esc(fu.get("lede", ""))}</p>')
        if w.get("timeline"):
            parts.append('<div class="sec"><h3>时间线</h3><ul class="tl">')
            for t, txt in w["timeline"]:
                parts.append(f"<li><b>{esc(t)}</b>{esc(txt)}</li>")
            parts.append("</ul></div>")
        for blk in w.get("blocks", []):
            cls = "box warn" if blk.get("type") == "warn" else "box"
            lines = "".join(f"<p>{esc(x)}</p>" for x in blk.get("lines", []))
            parts.append(f'<div class="sec"><h3>{esc(blk.get("title", ""))}</h3><div class="{cls}">{lines}</div></div>')
        if w.get("sources"):
            links = " · ".join(
                f'<a href="{esc(s.get("url", ""))}" target="_blank" rel="noopener">{esc(s.get("label", ""))}</a>'
                for s in w["sources"]
            )
            parts.append(f'<div class="sec"><h3>出处</h3><p class="src">{links}</p></div>')
        parts.append('<a class="top" href="#f1">↑ 回到本条</a></article>')

    parts.append(WEB_FOOT.format(date=esc(brief.get("date", "")), gen=datetime.now().strftime("%Y-%m-%d %H:%M")))
    return "\n".join(parts)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", default=str(ROOT / "config" / "brief.json"))
    parser.add_argument("--events", default=str(ROOT / "output" / "events.json"))
    parser.add_argument("--date", default=None)
    parser.add_argument("--base", default=None, help="网页版根地址，默认取 profile.toml [delivery].web_base")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    brief = load_json(Path(args.brief))
    date = args.date or brief.get("date") or datetime.now().date().isoformat()
    brief["date"] = date

    events_path = Path(args.events)
    events = load_json(events_path) if events_path.exists() else None

    errors, warns = validate(brief, events)
    for w in warns:
        print(f"  ⚠ {w}")
    if errors:
        print("\n校验未通过，未生成：")
        for e in errors:
            print(f"  ✗ {e}")
        return 2
    print(f"校验通过：{len(brief.get('items', []))} 条新闻 + {1 if brief.get('followup') else 0} 条后续")

    if args.dry_run:
        return 0

    base = args.base or load_profile().get("delivery", {}).get("web_base", "https://example.com/brief")
    base = base.rstrip("/")
    site_dir = ROOT / "site" / date
    site_dir.mkdir(parents=True, exist_ok=True)
    out_dir = ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    web = render_web(brief)
    (site_dir / "index.html").write_text(web, encoding="utf-8")
    send_card = render_card(brief, f"{base}/{date}/")
    (out_dir / f"card-{date}.html").write_text(send_card, encoding="utf-8")

    rel = render_card(brief, "index.html")
    (site_dir / "card-preview.html").write_text(rel, encoding="utf-8")
    print(f"网页版: {site_dir / 'index.html'}")
    print(f"发送用卡片: {out_dir / f'card-{date}.html'}（链接 {base}/{date}/#pN）")
    print(f"本地预览: {site_dir / 'card-preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

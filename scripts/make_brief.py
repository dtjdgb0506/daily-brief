"""从 output/events.json 自动生成 brief 初稿（抽取式，不依赖大模型）。

用法:
  python scripts/make_brief.py                          # 写 config/brief.draft.json
  python scripts/make_brief.py --out config/brief.json  # 直接覆盖正式稿（手改过的注意备份）
  python scripts/make_brief.py --date 2026-11-22

它做的是“把原文里最像导语/要点的句子抽出来”，标题、时间线、出处都来自原文，
所以能过 build_card.py 的校验（陈述句标题、数字有出处），但读起来会比手写糙。
用途：当天没人写 brief 时兜底，或者作为人工写的起点。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster_events import load_articles  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TAG_NAMES = {"tech": "科技", "finance": "财经", "politics": "政治", "other": "综合"}
SENT_SPLIT = re.compile(r"(?<=[。！？；])")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title.strip())
    t = t.rstrip("？?！!")
    if len(t) > 38:
        t = t[:38].rstrip("，,、 ")
    return t


def sentences(text: str) -> list[str]:
    parts = [s.strip() for s in SENT_SPLIT.split(text or "") if s.strip()]
    return [p for p in parts if 12 <= len(p) <= 140]


def has_digit(s: str) -> bool:
    return bool(re.search(r"\d", s))


def pick_lede(texts: list[str]) -> str:
    for text in texts:
        for s in sentences(text)[:6]:
            if has_digit(s) and len(s) >= 24:
                return s
    for text in texts:
        ss = sentences(text)
        if ss:
            return ss[0]
    return ""


def pick_points(texts: list[str], count: int = 3) -> list[list[str]]:
    chosen: list[str] = []
    for text in texts:
        for s in sentences(text):
            if len(s) < 20 or s in chosen:
                continue
            if has_digit(s) or any(k in s for k in ("宣布", "表示", "称", "发布", "辞职", "批准", "通过")):
                chosen.append(s)
            if len(chosen) >= count:
                break
        if len(chosen) >= count:
            break
    while len(chosen) < count and texts:
        for s in sentences(texts[0]):
            if s not in chosen:
                chosen.append(s)
                break
        break
    points = []
    for i, s in enumerate(chosen[:count], 1):
        s = s[:110].rstrip("，,、 ")
        points.append([f"{i} · ", s])
    return points


def pick_timeline(texts: list[str], limit: int = 8) -> list[list[str]]:
    """从正文里抓“11月20日/11/20”这类日期，配上后面的短句。"""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(r"(?:20\d{2}年)?(\d{1,2})月(\d{1,2})日|(\d{1,2})/(\d{1,2})")
    for text in texts:
        for s in sentences(text):
            m = pattern.search(s)
            if not m or s in seen:
                continue
            seen.add(s)
            label = f"{int(m.group(1))}/{int(m.group(2))}" if m.group(1) else f"{int(m.group(3))}/{int(m.group(4))}"
            body = s[:90].rstrip("，,、 ")
            found.append((label, body))
            if len(found) >= limit:
                return [[a, b] for a, b in found]
    return [[a, b] for a, b in found]


def build_item(event: dict, texts: dict[str, str], idx: int) -> dict:
    titles = event.get("titles", [event.get("representative_title", "")])
    urls = event.get("urls", [])
    rep = event.get("representative_title", "")
    # 代表标题那篇排最前，其次按正文长度：抽取导语时优先用“主稿”
    order = sorted(
        range(len(urls)),
        key=lambda k: (titles[k] != rep, -len(texts.get(urls[k], ""))),
    )
    body_pool = [texts.get(urls[k], "") for k in order]
    body_pool = [x for x in body_pool if x]
    lede = pick_lede(body_pool)
    points = pick_points(body_pool)
    timeline = pick_timeline(body_pool)
    sources = [
        {"label": titles[k][:36] or f"来源 {k+1}", "url": urls[k]}
        for k in order if urls[k]
    ][:5]
    return {
        "event_id": event["id"],
        "tag": TAG_NAMES.get(event["category"], "综合"),
        "tag_kind": event["category"] if event["category"] in ("tech", "finance", "politics") else "tech",
        "title": clean_title(event.get("representative_title", titles[0])),
        "lede": lede[:150],
        "points": points,
        "detail_hint": "时间线 · 出处",
        "web": {"timeline": timeline, "blocks": [], "sources": sources},
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default=str(ROOT / "output" / "events.json"))
    parser.add_argument("--raw", default=str(ROOT / "output" / "raw"))
    parser.add_argument("--out", default=str(ROOT / "config" / "brief.draft.json"))
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    data = load_json(Path(args.events))
    articles = load_articles(Path(args.raw))
    texts = {a["url"]: a["text"] for a in articles}

    by_id = {e["id"]: e for e in data.get("events", [])}
    items = []
    for eid in data.get("selected", []):
        if eid not in by_id:
            continue
        title = clean_title(by_id[eid].get("representative_title", ""))
        if len(title) < 6:
            continue
        items.append(build_item(by_id[eid], texts, len(items) + 1))
        if len(items) >= 5:
            break

    followup = None
    fid = data.get("followup")
    if fid and fid in by_id:
        fe = by_id[fid]
        fu = build_item(fe, texts, 0)
        fu.pop("event_id", None)
        fu["tag"] = "后续"
        fu["tag_kind"] = "fin"
        fu["section_title"] = f"过去 {data.get('history_days', 7)} 天的重要后续"
        fu["history"] = {"date": fe.get("followup_of", {}).get("date", ""), "title": fe.get("followup_of", {}).get("title", "")}
        followup = fu

    date = args.date or data.get("run_date") or datetime.now().date().isoformat()
    brief = {
        "date": date,
        "draft": True,
        "doc_title": items[0]["title"] if items else "每日情报",
        "preheader": f"今天 {len(items)} 条新闻" + (" + 1 条后续" if followup else "") + "（自动初稿）",
        "meta": f"{date} · {len(items)} 条新闻" + (" + 1 条后续" if followup else "") + " · 自动初稿",
        "items": items,
        "followup": followup,
    }

    out = Path(args.out)
    out.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"自动初稿：{out}（{len(items)} 条新闻 + {1 if followup else 0} 条后续）")
    for it in items:
        print(f"  - [{it['tag']}] {it['title']}")
    if followup:
        print(f"  ★ [后续] {followup['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

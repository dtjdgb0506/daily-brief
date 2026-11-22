"""把每个事件发布成独立的 telegra.ph 页面（无需账号、国内速度尚可）。

用法:
  python scripts/publish_telegraph.py                          # 读 config/brief.json
  python scripts/publish_telegraph.py --brief config/brief.json --out config/brief.published.json

产物:
  config/brief.published.json  原 brief + 每条 detail_url
页面链接再写进邮件卡片（build_card.py 会优先用 detail_url）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.telegra.ph/createPage"



def load_env() -> dict:
    path = ROOT / ".env"
    if not path.exists():
        return {}
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def ensure_token() -> str:
    """telegra.ph 现在发页必须带 account token；没有就自动开一个并写进 .env。"""
    cfg = load_env()
    token = cfg.get("TELEGRAPH_TOKEN", "")
    if token:
        return token
    data = urllib.parse.urlencode({
        "short_name": f"dailybrief{abs(hash('dailybrief')) % 10000}",
        "author_name": "每日情报",
    }).encode("utf-8")
    req = urllib.request.Request("https://api.telegra.ph/createAccount", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error"))
    token = payload["result"]["access_token"]
    with open(ROOT / ".env", "a", encoding="utf-8") as f:
        f.write(f"\n# telegra.ph 发布用\nTELEGRAPH_TOKEN={token}\n")
    print("已自动创建 telegra.ph 账号并写入 .env")
    return token



def p(text: str) -> dict:
    return {"tag": "p", "children": [text]}


def link(label: str, href: str) -> dict:
    return {"tag": "a", "attrs": {"href": href}, "children": [label]}


def nodes_for(item: dict, date: str, followup: bool = False) -> list[dict]:
    nodes: list[dict] = []
    if followup:
        hist = item.get("history") or {}
        if hist:
            nodes.append(p(f"后续自 {hist.get('date', '')}：{hist.get('title', '')}"))
    lede = item.get("lede", "")
    if lede:
        nodes.append({"tag": "blockquote", "children": [lede]})

    w = item.get("web") or {}
    if w.get("timeline"):
        nodes.append({"tag": "h3", "children": ["时间线"]})
        for d, txt in w["timeline"]:
            nodes.append(p(f"{d}  {txt}"))

    for blk in w.get("blocks", []):
        nodes.append({"tag": "h3", "children": [blk.get("title", "分析")]})
        for line in blk.get("lines", []):
            nodes.append(p(line))

    if w.get("sources"):
        nodes.append({"tag": "h3", "children": ["出处"]})
        for s in w["sources"]:
            nodes.append({"tag": "p", "children": [link(s.get("label", "原文"), s.get("url", ""))]})

    nodes.append({"tag": "hr"})
    nodes.append(p(f"每日情报 · {date}" + (" · 过去 7 天的重要后续" if followup else "")))
    return nodes


def create_page(title: str, nodes: list[dict], token: str) -> str:
    data = urllib.parse.urlencode({
        "access_token": token,
        "title": title[:256],
        "author_name": "每日情报",
        "content": json.dumps(nodes, ensure_ascii=False),
        "return_content": "false",
    }).encode("utf-8")
    req = urllib.request.Request(API, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error"))
    return payload["result"]["url"]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", default=str(ROOT / "config" / "brief.json"))
    parser.add_argument("--out", default=str(ROOT / "config" / "brief.published.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8-sig"))
    date = brief.get("date", "")
    token = ensure_token()

    if args.dry_run:
        for idx, it in enumerate(brief.get("items", []), 1):
            print(f"p{idx}: {it.get('title')} -> {len(nodes_for(it, date))} 个节点")
        return 0

    for idx, it in enumerate(brief.get("items", []), 1):
        url = create_page(it.get("title", f"每日情报 {date} 第 {idx} 条"), nodes_for(it, date), token)
        it["detail_url"] = url
        print(f"p{idx} {url}")

    fu = brief.get("followup")
    if fu:
        url = create_page(fu.get("title", f"每日情报 {date} 后续"), nodes_for(fu, date, followup=True), token)
        fu["detail_url"] = url
        print(f"f1 {url}")

    Path(args.out).write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

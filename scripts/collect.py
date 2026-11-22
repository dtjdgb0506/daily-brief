"""从 config/sources.toml 的 RSS/Atom 源收集候选文章，可选把正文抓回来入库。

用法:
  python scripts/collect.py                      # 只出候选清单
  python scripts/collect.py --fetch 30           # 顺手抓最近 30 篇正文
  python scripts/collect.py --since-hours 24 --fetch 30
  python scripts/collect.py --dry-run

输出:
  output/candidates/<YYYY-MM-DD>.json   [{title,url,feed,category,published}]
  output/raw/<YYYY-MM-DD>/              抓了正文才会写（复用 fetch_article.save_article）
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_article import (  # noqa: E402
    ROOT,
    UA,
    canonical,
    extract,
    fetch_html,
    save_article,
)

CST = timezone(timedelta(hours=8))


def load_sources() -> list[dict]:
    path = ROOT / "config" / "sources.toml"
    if not path.exists():
        return []
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        print("需要 Python 3.11+ 的 tomllib")
        return []
    with path.open("rb") as f:
        return tomllib.load(f).get("feed", [])


def fetch_feed(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "ignore")


def _text(node, tag: str) -> str:
    el = node.find(tag)
    if el is not None and el.text:
        return el.text.strip()
    # Atom: <title type="html">
    for child in node:
        if child.tag.endswith("}" + tag) and child.text:
            return child.text.strip()
    return ""


def _link(node) -> str:
    el = node.find("link")
    if el is not None and el.text:
        return el.text.strip()
    for child in node:
        if child.tag.endswith("}link"):
            return (child.get("href") or "").strip()
    return ""


def parse_feed(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = _text(node, "title")
        link = _link(node)
        date_s = _text(node, "pubDate") or _text(node, "published") or _text(node, "updated") or ""
        published = ""
        if date_s:
            try:
                dt = parsedate_to_datetime(date_s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CST)
                published = dt.astimezone(CST).isoformat(timespec="seconds")
            except (TypeError, ValueError):
                m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", date_s)
                if m:
                    published = "-".join(f"{int(x):02d}" for x in m.groups()) + "T00:00:00+08:00"
        if not published and link:
            m = re.search(r"/(20\d{2})-?(\d{2})/(\d{2})/", link) or re.search(r"/(20\d{2})(\d{2})(\d{2})/", link)
            if m:
                y, mo, d = (int(x) for x in m.groups())
                try:
                    from datetime import date as _date
                    published = _date(y, mo, d).isoformat() + "T00:00:00+08:00"
                except ValueError:
                    pass
        if title and link:
            items.append({"title": title, "url": link, "published": published})
    return items


def pick_todo(candidates: list[dict], limit: int, per_feed: int) -> list[dict]:
    """每个源最多取 per_feed 篇（按发布时间新→旧），再整体按时间排序截断。

    旧逻辑是严格轮转、每个源每轮只取 1 篇：抓 40 篇时 49 个源里有一半一篇都轮不到，
    而像 The Hill 这种一次给 58 条候选的源，也只会抓 1 条。结果是“候选池 245 条、
    正文只有 40 篇”，大事很可能根本没被抓到。
    """
    by_feed: dict[str, list[dict]] = {}
    for c in candidates:
        by_feed.setdefault(c.get("feed", "?"), []).append(c)
    picked: list[dict] = []
    for items in by_feed.values():
        items.sort(key=lambda c: c.get("published") or "", reverse=True)
        picked.extend(items[:per_feed])
    picked.sort(key=lambda c: c.get("published") or "", reverse=True)
    return picked[:limit]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", type=int, default=0, help="抓多少篇正文（0=只出候选）")
    parser.add_argument("--per-feed", type=int, default=4, help="每个源最多抓几篇正文（默认 4）")
    parser.add_argument("--feed-timeout", type=int, default=15, help="单个 RSS 源的超时秒数（本机被墙时可调小）")
    parser.add_argument("--since-hours", type=int, default=48)
    parser.add_argument("--date", default=None, help="入库日期目录，默认今天")
    parser.add_argument("--day", default=None, help="只抓这一天的候选（YYYY-MM-DD），默认不限制")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    feeds = load_sources()
    if not feeds:
        print("config/sources.toml 里没有 [[feed]]")
        return 1

    now = datetime.now(CST)
    cutoff = now - timedelta(hours=args.since_hours)
    seen: set[str] = set()
    candidates: list[dict] = []
    stats = []

    for feed in feeds:
        name = feed.get("name", feed.get("url", "?"))
        try:
            xml_text = fetch_feed(feed["url"], timeout=args.feed_timeout)
        except Exception as exc:  # noqa: BLE001
            stats.append((name, 0, f"FAIL {type(exc).__name__}"))
            continue
        items = parse_feed(xml_text)
        kept = 0
        for it in items:
            key = canonical(it["url"])
            if key in seen:
                continue
            pub = it.get("published") or ""
            if pub:
                try:
                    dt = datetime.fromisoformat(pub)
                    if dt < cutoff:
                        continue
                except ValueError:
                    pass
            seen.add(key)
            it["feed"] = name
            it["category"] = feed.get("category", "")
            candidates.append(it)
            kept += 1
        stats.append((name, kept, "ok"))

    candidates.sort(key=lambda c: c.get("published") or "", reverse=True)
    if args.day:
        same_day = [c for c in candidates if (c.get("published") or "")[:10] == args.day]
        print(f"  {args.day} 当天的候选：{len(same_day)}/{len(candidates)} 条")
        candidates = same_day
    day = args.date or now.date().isoformat()
    out = ROOT / "output" / "candidates" / f"{day}.json"
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"源 {len(feeds)} 个，候选 {len(candidates)} 条（{args.since_hours}h 内，已按 URL 去重）")
    for name, kept, note in stats:
        print(f"  {name:<12} {kept:>3} 条  {note}")
    print(f"候选清单: {out}" if not args.dry_run else "(dry-run，未写清单)")

    if args.fetch and candidates and not args.dry_run:
        todo = pick_todo(candidates, args.fetch, args.per_feed)
        print(f"\n抓正文 {len(todo)} 篇 ...")
        ok = 0
        for it in todo:
            try:
                html, _ = fetch_html(it["url"])
                doc = extract(html, it["url"])
                save_article(
                    it["url"],
                    doc["title"] or it["title"],
                    doc["text"],
                    doc["published"] or (it.get("published") or "")[:10],
                    it.get("feed", ""),
                    day,
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {it['url']} {type(exc).__name__}")
        print(f"入库 {ok}/{len(todo)} 篇 -> output/raw/{day}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

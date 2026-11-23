"""按事件归档重新抓正文，用于「补发/重做某天」。

用法:
  python scripts/refetch_archive.py --archive state/archive/2026-11-23/events.json \
      --ids e3,e5,e12 --day 2026-11-22

会把每个事件下的原始 URL 重新抓一遍，存进 output/raw/<day>/（带发布日期），
之后可以自己组 events.json → make_brief → build_card。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_article import extract, fetch_html, save_article  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, help="events.json 路径")
    parser.add_argument("--ids", required=True, help="逗号分隔的事件 id，如 e3,e5,e12")
    parser.add_argument("--day", required=True, help="存到 output/raw/<day>/（内容日）")
    parser.add_argument("--extra-url", action="append", default=[], help="额外抓的 URL，可重复")
    args = parser.parse_args()

    data = json.loads(Path(args.archive).read_text(encoding="utf-8-sig"))
    by = {e["id"]: e for e in data.get("events", [])}
    urls: list[str] = []
    for eid in args.ids.split(","):
        event = by.get(eid.strip())
        if event:
            urls.extend(event.get("urls", []))
        else:
            print(f"  ? 归档里没有 {eid}")
    urls.extend(args.extra_url)

    seen: set[str] = set()
    ok = 0
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        try:
            html, _ = fetch_html(url)
            doc = extract(html, url)
            save_article(url, doc["title"], doc["text"], doc["published"] or "", "", args.day)
            ok += 1
            print(f"  OK  {doc['published'] or '?'}  {url[:70]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {type(exc).__name__}  {url[:70]}")
    print(f"入库 {ok}/{len(urls)} -> output/raw/{args.day}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

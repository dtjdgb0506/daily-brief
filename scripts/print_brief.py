"""打印一期 brief 的条目，供日志/人工快速检查。

用法: python scripts/print_brief.py config/brief.json
"""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/print_brief.py <brief.json>")
        return 2
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    print(f"issue_date={data.get('date')} content_day={data.get('content_day', '?')}")
    print(f"doc_title={data.get('doc_title')}")
    for i, it in enumerate(data.get("items", []), 1):
        blocks = [b.get("title") for b in (it.get("web") or {}).get("blocks", [])]
        print(f"{i}. [{it.get('tag')}] {it.get('title')}  (blocks: {blocks})")
    fu = data.get("followup")
    if fu:
        print(f"★ 后续: {fu.get('title')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

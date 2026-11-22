"""并发检查 config/sources.toml 里每个 RSS 源能不能用、能出多少条。

用法:
  python scripts/check_sources.py              # 默认 10 秒超时、12 并发
  python scripts/check_sources.py --timeout 6 --workers 16
  python scripts/check_sources.py --only core  # 只看某个 tier

说明：国内直连访问境外源会超时，属正常（每日任务跑在 GitHub Actions 的美国服务器上）。
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from collect import fetch_feed, parse_feed  # noqa: E402


def load_feeds() -> list[dict]:
    path = ROOT / "config" / "sources.toml"
    with path.open("rb") as f:
        return tomllib.load(f).get("feed", [])


def check(feed: dict, timeout: int) -> tuple[str, str, int, str]:
    name = feed.get("name", feed.get("url", "?"))
    tier = feed.get("tier", "cn")
    try:
        items = parse_feed(fetch_feed(feed["url"], timeout=timeout))
        return name, tier, len(items), "" if items else "空 feed"
    except Exception as exc:  # noqa: BLE001
        return name, tier, 0, f"{type(exc).__name__}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--only", default=None, help="只看某个 tier，如 core/tech/analysis/cn")
    args = parser.parse_args()

    feeds = load_feeds()
    if args.only:
        feeds = [f for f in feeds if f.get("tier") == args.only]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda f: check(f, args.timeout), feeds))

    ok = [r for r in results if r[2] > 0]
    results.sort(key=lambda r: (r[2] == 0, -r[2]))
    for name, tier, count, note in results:
        flag = f"{count:>3} 条" if count else "  ×  "
        print(f"  {flag}  [{tier:<8}] {name}" + (f"  ({note})" if note else ""))
    print(f"\n可用 {len(ok)}/{len(results)} 个源，共 {sum(r[2] for r in ok)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())

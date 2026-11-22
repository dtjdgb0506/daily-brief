"""准点发信：只读 06:50 准备好的卡片发出去，不抓取、不生成。

用法:
  python scripts/send_from_state.py            # 发今天的
  python scripts/send_from_state.py --date 2026-11-23
  python scripts/send_from_state.py --force    # 已发过也再发一次

为什么要单独一个脚本：
  抓取+改写+出卡要 4~8 分钟，放在 07:30 才开始跑就晚了。
  06:50 先跑完存到 state/cards/<日期>.html，07:30 这一步只发信，10~20 秒到。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD_DIR = ROOT / "state" / "cards"
STATE = ROOT / "state" / "sent.json"


def load_sent() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_sent(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只检查卡片在不在，不发信")
    args = parser.parse_args()

    card = CARD_DIR / f"{args.date}.html"
    meta_path = CARD_DIR / f"{args.date}.json"
    if not card.exists():
        print(f"✗ 没有准备好的卡片：{card}（06:50 的准备任务可能没跑成）")
        return 1

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    subject = meta.get("subject") or f"每日情报 {args.date[5:].replace('-', '·')}"

    sent = load_sent()
    if not args.force and args.date in sent:
        print(f"▶ {args.date} 已在 {sent[args.date].get('sent_at')} 发过，跳过（--force 可强制）")
        return 0

    if args.dry_run:
        print(f"[dry-run] 会发：{subject}  ←  {card.name}（{card.stat().st_size} 字节）")
        return 0

    print(f"▶ 发信：{subject}")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "send_html.py"), "--subject", subject, "--html", str(card)],
        cwd=ROOT,
    )
    if proc.returncode != 0:
        print("✗ 发信失败")
        return proc.returncode

    sent[args.date] = {"sent_at": datetime.now().isoformat(timespec="seconds"), "subject": subject}
    save_sent(sent)
    print(f"✓ {args.date} 已发送")
    return 0


if __name__ == "__main__":
    sys.exit(main())

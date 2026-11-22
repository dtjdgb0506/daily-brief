"""一天一条命令：采集 → 聚类 → 出卡 → 发信。

用法:
  python scripts/run_daily.py --dry-run          # 全流程跑一遍，不发信
  python scripts/run_daily.py                    # 真发
  python scripts/run_daily.py --no-collect       # 跳过抓取，用现有 raw
  python scripts/run_daily.py --force            # 今天已经发过也再发一次

行为:
  - 所有步骤输出同时写到 logs/<日期>.log
  - state/sent.json 记录发过的日期；重复运行默认跳过发信（幂等）
  - brief.json 的日期不是今天 → 直接失败（提示先写 brief）
  - 任一步骤失败 → 非 0 退出，日志里能看到原因
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
STATE = ROOT / "state" / "sent.json"
LOGS = ROOT / "logs"


def log(lines: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_step(name: str, args: list[str], log_path: Path) -> tuple[bool, str]:
    cmd = [PY] + args
    print(f"\n▶ {name}: {' '.join(Path(a).name if a.endswith('.py') else a for a in args)}")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.rstrip())
    log([f"===== {name} =====", out.rstrip(), f"exit={proc.returncode}"], log_path)
    return proc.returncode == 0, out


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
    parser.add_argument("--date", default=datetime.now().date().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-collect", action="store_true")
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fetch", type=int, default=30, help="采集后抓多少篇正文")
    parser.add_argument("--window-hours", type=int, default=24)
    args = parser.parse_args()

    date = args.date
    log_path = LOGS / f"{date}.log"
    started = datetime.now()
    print(f"每日情报 · {date} · {started:%H:%M:%S}")

    # 1. 采集
    if not args.no_collect:
        ok, _ = run_step("采集", ["scripts/collect.py", "--fetch", str(args.fetch)], log_path)
        if not ok:
            print("  ⚠ 采集失败，继续用现有 raw 数据")
    else:
        print("\n▶ 采集: 跳过（--no-collect）")

    # 2. 聚类（含“前七天后续”）
    ok, _ = run_step("聚类", ["scripts/cluster_events.py", "--window-hours", str(args.window_hours)], log_path)
    if not ok:
        print("✗ 聚类失败，终止")
        return 1

    # 3. 出卡：brief 必须是当天的
    brief_path = ROOT / "config" / "brief.json"
    if not brief_path.exists():
        print(f"✗ 缺少 {brief_path}：这一步要人工/模型把 events.md 写成 brief.json")
        return 1
    try:
        brief = json.loads(brief_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        print(f"✗ brief.json 解析失败：{exc}")
        return 1
    if brief.get("date") != date:
        print(f"✗ brief.json 的 date={brief.get('date')}，不是今天（{date}）。先更新 brief 再发。")
        return 1

    ok, _ = run_step("出卡", ["scripts/build_card.py", "--date", date], log_path)
    if not ok:
        print("✗ 出卡失败（多半是校验没通过），终止")
        return 1

    card = ROOT / "output" / f"card-{date}.html"
    if not card.exists():
        print(f"✗ 找不到 {card}")
        return 1

    # 4. 发信（幂等）
    sent = load_sent()
    if not args.force and date in sent:
        print(f"\n▶ 发信: {date} 已在 {sent[date].get('sent_at')} 发过，跳过（--force 可强制重发）")
        return 0
    if args.dry_run or args.no_send:
        print(f"\n▶ 发信: 跳过（{'dry-run' if args.dry_run else '--no-send'}），卡片在 {card}")
        return 0

    subject = f"每日情报 {date[5:].replace('-', '·')}｜{brief.get('doc_title', '')[:24]}"
    ok, _ = run_step("发信", ["scripts/send_html.py", "--subject", subject, "--html", str(card)], log_path)
    if not ok:
        print("✗ 发信失败，详见日志")
        return 1
    sent[date] = {"sent_at": datetime.now().isoformat(timespec="seconds"), "subject": subject}
    save_sent(sent)
    print(f"\n✓ {date} 已发送")
    return 0


if __name__ == "__main__":
    sys.exit(main())

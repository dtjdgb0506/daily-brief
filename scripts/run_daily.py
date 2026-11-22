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
import os
import subprocess
import sys
from datetime import datetime, timedelta
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


def llm_configured() -> bool:
    env = ROOT / ".env"
    if not env.exists():
        return bool(os.environ.get("LLM_API_KEY"))
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("LLM_API_KEY="):
            return bool(line.split("=", 1)[1].strip())
    return bool(os.environ.get("LLM_API_KEY"))


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
    parser.add_argument("--auto-brief", action="store_true", help="当天没有 brief 时用 make_brief.py 生成自动初稿")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--same-day", action="store_true", help="默认推前一天的新闻；加这个才推当天")
    args = parser.parse_args()

    date = args.date
    log_path = LOGS / f"{date}.log"
    started = datetime.now()
    print(f"每日情报 · {date} · {started:%H:%M:%S}")

    # 1. 采集（先算内容日：默认前一天）
    content_day = date if args.same_day else (
        datetime.fromisoformat(date) - timedelta(days=1)).date().isoformat()
    print(f"  内容日：{content_day}（{'当天' if args.same_day else '前一天'}）")
    if not args.no_collect:
        ok, _ = run_step("采集", ["scripts/collect.py", "--fetch", str(args.fetch),
                                 "--day", content_day], log_path)
        if not ok:
            print("  ⚠ 采集失败，继续用现有 raw 数据")
    else:
        print("\n▶ 采集: 跳过（--no-collect）")

    # 2. 聚类（默认取“昨天”全天的新闻：分析文章需要时间，早上能看到昨日+分析）
    ok, _ = run_step("聚类", ["scripts/cluster_events.py", "--content-day", content_day,
                             "--window-hours", str(args.window_hours)], log_path)
    if not ok:
        print(f"  ⚠ {content_day} 没有稿子，回退到最近 {args.window_hours}h（保证每天能发）")
        ok, _ = run_step("聚类（回退）", ["scripts/cluster_events.py",
                                       "--window-hours", str(args.window_hours)], log_path)
        if not ok:
            print("✗ 聚类失败，终止")
            return 1

    # 3. 出卡：优先用人工稿；没有/过期时可以自动生成初稿
    brief_path = ROOT / "config" / "brief.json"
    need_brief = True
    if brief_path.exists():
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8-sig"))
            need_brief = brief.get("date") != date
        except json.JSONDecodeError:
            need_brief = True
    if need_brief:
        if not args.auto_brief:
            print(f"✗ config/brief.json 缺失或不是今天（{date}）。"
                  f"\n  要么先写 brief，要么加 --auto-brief 用抽取式初稿兜底。")
            return 1
        draft = ROOT / "config" / "brief.draft.json"
        ok, _ = run_step("自动初稿", ["scripts/make_brief.py", "--date", date], log_path)
        if not ok or not draft.exists():
            print("✗ 自动初稿生成失败")
            return 1
        brief_path = draft
        print("  ⚠ 用的是自动初稿（抽取式），建议抽查后再发。")
        if llm_configured():
            auto_path = ROOT / "config" / "brief.auto.json"
            ok, _ = run_step("模型改写", ["scripts/llm_polish.py",
                                         "--in", str(draft),
                                         "--out", str(auto_path)], log_path)
            if ok:
                brief_path = auto_path
                print("  ✓ 已用模型改写（brief.auto.json，不动手写稿）")
            else:
                print("  ⚠ 模型改写失败，继续用抽取式初稿")

    # 4. 备用：发布到 telegra.ph（默认关闭；国内要 VPN，仅 TELEGRAPH_PUBLISH=1 时启用）
    import os as _os
    if _os.environ.get("TELEGRAPH_PUBLISH") == "1":
        published = ROOT / "config" / "brief.published.json"
        ok, _ = run_step("发布详情页", ["scripts/publish_telegraph.py", "--brief", str(brief_path)], log_path)
        if ok and published.exists():
            brief_path = published

    ok, _ = run_step("出卡", ["scripts/build_card.py", "--date", date, "--brief", str(brief_path)], log_path)
    if not ok:
        print("  ⚠ 出卡校验没过，用 --lenient 重试（保证每天都能发）")
        ok, _ = run_step("出卡（宽松）", ["scripts/build_card.py", "--date", date, "--brief", str(brief_path), "--lenient"], log_path)
        if not ok:
            print("✗ 出卡仍然失败，终止")
            return 1

    card = ROOT / "output" / f"card-{date}.html"
    if not card.exists():
        print(f"✗ 找不到 {card}")
        return 1

    # 5. 部署网页版（配了腾讯云 CloudBase 才部署；邮件里的链接指向它）
    env_text = (ROOT / ".env").read_text(encoding="utf-8") if (ROOT / ".env").exists() else ""
    if "TCB_ENV_ID" in env_text or _os.environ.get("TCB_ENV_ID"):
        ok, _ = run_step("部署网页版", ["scripts/deploy_cloudbase.py"], log_path)
        if not ok:
            print("  ⚠ 网页版部署失败：回退成“邮件内联详情”，保证这封邮件能看")
            run_step("回退出卡（内联）", ["scripts/build_card.py", "--date", date,
                                       "--brief", str(brief_path), "--base", ""], log_path)
    else:
        print("\n▶ 部署网页版: 跳过（未配置 TCB_ENV_ID）")

    # 6. 发信（幂等）
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

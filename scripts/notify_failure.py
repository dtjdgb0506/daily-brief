"""流水线失败时发一封“今天没发成”的提醒（不带附件，只为让你知道）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from send_html import build_message, deliver, load_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

HTML = """<!doctype html><html><body style="margin:0;padding:24px;background:#f4f5f7;
 font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">
 <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;padding:24px;">
  <div style="font-size:12px;letter-spacing:2px;color:#94a3b8;">DAILY BRIEF</div>
  <h2 style="margin:8px 0 12px;font-size:20px;color:#0f172a;">今天的情报没发成</h2>
  <p style="font-size:14px;line-height:1.8;color:#334155;">
    云端流水线在生成或发送时失败了，具体原因看仓库的 Actions 运行日志。
    常见原因：采集源当天全部抓取失败、或 163 邮箱拒绝登录。
  </p>
 </div></body></html>"""


def main() -> int:
    cfg = load_env(ROOT / ".env")
    msg = build_message(cfg, "【失败】每日情报没发出去", HTML, None, cfg["MAIL_TO"], None)
    deliver(cfg, msg, dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

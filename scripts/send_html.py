"""发送一封 HTML 邮件（复用同一套 .env 配置）。

用法:
  python scripts/send_html.py --subject "..." --html output/card-2026-11-22.html
  python scripts/send_html.py --subject "..." --html x.html --to a@b.com --cc c@d.com
  python scripts/send_html.py --subject "..." --html x.html --dry-run

特性:
  - 自动补 Date / Message-ID 头（很多邮箱缺这两个会更容易被判垃圾）
  - 465 SSL 失败时自动回退 587 STARTTLS
  - 失败重试 3 次（间隔 2s / 4s）
  - --dry-run 只打印，不连服务器
"""

from __future__ import annotations

import argparse
import hashlib
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> dict:
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cfg[key.strip()] = value.strip()
    return cfg


def html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def build_message(cfg: dict, subject: str, html: str, text: str | None,
                  to_addr: str, cc: str | None) -> EmailMessage:
    sender = cfg.get("MAIL_FROM", cfg["MAIL_USER"])
    from_name = cfg.get("MAIL_FROM_NAME", "每日情报")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f'"{from_name}" <{sender}>'
    msg["To"] = to_addr
    if cc:
        msg["Cc"] = cc
    msg["Date"] = formatdate(localtime=True)
    # 幂等：同一天 + 同一主题 + 同一收件人 → 相同 Message-ID。
    # 即使重复运行、真的发了两遍，邮箱也会把第二封折叠成一封，而不是两条未读。
    token = hashlib.sha1(f"{subject}|{to_addr}".encode("utf-8")).hexdigest()[:12]
    msg["Message-ID"] = f"<daily-brief-{token}@dailybrief.local>"
    msg.set_content(text or html_to_text(html))
    msg.add_alternative(html, subtype="html")
    return msg


def deliver(cfg: dict, msg: EmailMessage, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] 将发送给 {msg['To']}，主题：{msg['Subject']}")
        return

    host = cfg["MAIL_SMTP_HOST"]
    last_error: Exception | None = None
    for attempt in range(1, 4):
        for mode, port in (("ssl", int(cfg.get("MAIL_SMTP_PORT", 465))),
                           ("starttls", int(cfg.get("MAIL_SMTP_PORT_STARTTLS", 587)))):
            try:
                ctx = ssl.create_default_context()
                if mode == "ssl":
                    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as server:
                        server.login(cfg["MAIL_USER"], cfg["MAIL_PASS"])
                        server.send_message(msg)
                else:
                    with smtplib.SMTP(host, port, timeout=30) as server:
                        server.starttls(context=ctx)
                        server.login(cfg["MAIL_USER"], cfg["MAIL_PASS"])
                        server.send_message(msg)
                print(f"发送成功（{mode}:{port}，第 {attempt} 次尝试）")
                return
            except (smtplib.SMTPException, OSError) as exc:
                last_error = exc
                print(f"  {mode}:{port} 失败：{type(exc).__name__}: {exc}")
        time.sleep(2 ** attempt)
    raise RuntimeError(f"3 次重试都失败：{last_error}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--to", default=None, help="多个收件人用逗号分隔；会拆成一人一封")
    parser.add_argument("--cc", default=None)
    parser.add_argument("--text", default=None, help="纯文本正文（默认由 HTML 自动生成）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_env(ROOT / ".env")
    html_path = Path(args.html)
    if not html_path.is_absolute():
        html_path = ROOT / html_path
    html = html_path.read_text(encoding="utf-8")

    # 一人一封：发给自己那封会被 163 收进“我的收藏助手”，发给别人的是正常来信
    recipients = [x.strip() for x in (args.to or cfg["MAIL_TO"]).split(",") if x.strip()]
    failed = []
    for addr in recipients:
        msg = build_message(cfg, args.subject, html, args.text, addr, args.cc)
        try:
            deliver(cfg, msg, args.dry_run)
            if not args.dry_run:
                print(f"发送成功 -> {addr} | 主题: {args.subject}")
        except Exception as exc:  # noqa: BLE001
            failed.append(addr)
            print(f"发送失败 -> {addr}：{type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

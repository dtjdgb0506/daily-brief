"""发送一封 HTML 邮件（复用同一套 .env 配置）。

用法: python scripts/send_html.py --subject "..." --html output/xxx.html [--to 地址] [--text 纯文本]
"""

import argparse
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--to", default=None)
    parser.add_argument("--text", default=None)
    args = parser.parse_args()

    cfg = load_env(ROOT / ".env")
    html_path = Path(args.html)
    if not html_path.is_absolute():
        html_path = ROOT / html_path
    html = html_path.read_text(encoding="utf-8")

    to_addr = args.to or cfg["MAIL_TO"]
    sender = cfg.get("MAIL_FROM", cfg["MAIL_USER"])
    from_name = cfg.get("MAIL_FROM_NAME", "每日情报")

    msg = EmailMessage()
    msg["Subject"] = args.subject
    msg["From"] = f'"{from_name}" <{sender}>'
    msg["To"] = to_addr
    msg.set_content(args.text or html_to_text(html))
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["MAIL_SMTP_HOST"], int(cfg["MAIL_SMTP_PORT"]), context=ctx, timeout=30) as server:
        server.login(cfg["MAIL_USER"], cfg["MAIL_PASS"])
        server.send_message(msg)
    print(f"发送成功 -> {to_addr} | 主题: {args.subject}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

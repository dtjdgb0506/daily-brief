"""发信通道自检：验证 163 SMTP 授权码是否可用，并真实发出一封测试信。"""

import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HTML = """<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f5f7;font-family:-apple-system,Segoe UI,Helvetica,Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;">
    <tr><td style="padding:20px 28px;background:#0f172a;color:#ffffff;">
      <div style="font-size:13px;letter-spacing:2px;color:#94a3b8;">DAILY BRIEF</div>
      <div style="font-size:22px;font-weight:600;margin-top:6px;">发信通道已打通</div>
    </td></tr>
    <tr><td style="padding:24px 28px;color:#1e293b;font-size:15px;line-height:1.8;">
      <p style="margin:0 0 12px;">如果你在收件箱里看到这封信，说明 SMTP 通道正常，后面每日情报就可以从这里发出去了。</p>
      <p style="margin:0 0 12px;color:#64748b;font-size:13px;">这条通道的意义不只是"能发信"——它意味着整条链路（服务器 → 授权码 → 你的收件箱）是通的。</p>
      <p style="margin:0;">下一步：我会先做一张真实事件的样板卡，你确认格式之后再谈每天自动推送。</p>
    </td></tr>
    <tr><td style="padding:16px 28px;background:#f8fafc;color:#94a3b8;font-size:12px;">这是系统自检邮件，不用回复。</td></tr>
  </table>
</body></html>"""


def load_env(path: Path) -> dict:
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cfg[key.strip()] = value.strip()
    return cfg


def main() -> int:
    cfg = load_env(ROOT / ".env")
    host = cfg["MAIL_SMTP_HOST"]
    port = int(cfg["MAIL_SMTP_PORT"])
    user = cfg["MAIL_USER"]
    password = cfg["MAIL_PASS"]
    sender = cfg.get("MAIL_FROM", user)
    to_addr = cfg["MAIL_TO"]
    from_name = cfg.get("MAIL_FROM_NAME", "每日情报")

    msg = EmailMessage()
    msg["Subject"] = "【测试】每日情报 · 发信通道已打通"
    msg["From"] = f'"{from_name}" <{sender}>'
    msg["To"] = to_addr
    msg.set_content("如果你看到这封信，说明发信通道正常。这是纯文本兜底版本。")
    msg.add_alternative(HTML, subtype="html")

    ctx = ssl.create_default_context()
    print(f"连接 {host}:{port} ...")
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as server:
        server.login(user, password)
        print("登录成功")
        server.send_message(msg)
    print(f"发送成功 -> {to_addr}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

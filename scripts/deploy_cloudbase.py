"""把 site/ 部署到腾讯云 CloudBase（国内直连，默认域名即可访问）。

读环境变量（或 .env）：TCB_ENV_ID / TCB_SECRET_ID / TCB_SECRET_KEY
没有密钥时，也可以用 `tcb login` 的手机/浏览器登录态（本机已登录过）。

用法:
  python scripts/deploy_cloudbase.py
  python scripts/deploy_cloudbase.py --site site
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NODE = Path(r"C:\Users\ROG\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe")
PNPM = Path(r"C:\Users\ROG\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs")


def load_env() -> dict:
    cfg = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    for k in ("TCB_ENV_ID", "TCB_SECRET_ID", "TCB_SECRET_KEY"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def tcb_cmd() -> list[str]:
    if NODE.exists() and PNPM.exists():
        return [str(NODE), str(PNPM), "--package=@cloudbase/cli", "dlx", "tcb"]
    return ["npx", "--yes", "@cloudbase/cli@latest"]


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd[:6]), "...")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.strip():
            print("  " + line.strip()[:200])
    return proc.returncode


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="site")
    args = parser.parse_args()

    cfg = load_env()
    env_id = cfg.get("TCB_ENV_ID", "")
    if not env_id:
        print("没配 TCB_ENV_ID（.env 或环境变量），跳过部署")
        return 1

    base = tcb_cmd()
    if cfg.get("TCB_SECRET_ID") and cfg.get("TCB_SECRET_KEY"):
        rc = run(base + ["login", "--apiKeyId", cfg["TCB_SECRET_ID"], "--apiKey", cfg["TCB_SECRET_KEY"]])
        if rc != 0:
            print("登录失败")
            return rc
    rc = run(base + ["hosting", "deploy", args.site, "/", "-e", env_id])
    if rc == 0:
        print(f"部署完成：https://{env_id}-1538638844.tcloudbaseapp.com/")
    return rc


if __name__ == "__main__":
    sys.exit(main())

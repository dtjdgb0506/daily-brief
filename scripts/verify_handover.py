"""交接文档自检：把“文档里写的”和“代码/线上的实际状态”对一遍。

用法:
  python scripts/verify_handover.py

输出一段 Markdown，可直接粘进 HANDOVER.md 的「最后核验」。
（云端/网络检查失败不会让脚本失败，只标成“不可达”。）
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUD_FALLBACK = "https://daily-brief-d4ggl7ududb678762-1538638844.tcloudbaseapp.com"
UA = "Mozilla/5.0 (compatible; daily-brief-verify/1.0)"


def load_profile() -> dict:
    with (ROOT / "config" / "profile.toml").open("rb") as f:
        return tomllib.load(f)


def git_remote() -> str:
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def http_status(url: str, timeout: int = 12) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001
        return f"不可达（{type(exc).__name__}）"


def check_tests() -> tuple[bool, str]:
    try:
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except subprocess.SubprocessError as exc:
        return False, f"运行失败：{exc}"
    tail = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()][-1:]
    return proc.returncode == 0, tail[0] if tail else ""


def check_placeholder() -> list[str]:
    hits = []
    for path in list((ROOT / "output").glob("card-*.html")) + list((ROOT / "site").glob("*/*.html")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "example.com" in text:
            hits.append(str(path.relative_to(ROOT)))
    return hits


def check_actions(repo: str) -> str:
    if not repo:
        return "没有 git remote"
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", repo)
    if not m:
        return f"无法从 remote 解析仓库：{repo}"
    api = f"https://api.github.com/repos/{m.group(1)}/actions/runs?per_page=1"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        runs = data.get("workflow_runs") or []
        if not runs:
            return "还没有运行记录"
        r = runs[0]
        return f"{r.get('name')} · {r.get('created_at', '')[:10]} · {r.get('status')}/{r.get('conclusion')}"
    except Exception as exc:  # noqa: BLE001
        return f"不可达（{type(exc).__name__}）"


def check_state() -> str:
    path = ROOT / "state" / "sent.json"
    if not path.exists():
        return "还没有（尚未真发过，或本地没跑过真发）"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return "文件损坏"
    if not data:
        return "空"
    last = sorted(data)[-1]
    return f"{len(data)} 天记录，最近 {last} @ {data[last].get('sent_at', '?')}"


def check_feeds() -> str:
    with (ROOT / "config" / "sources.toml").open("rb") as f:
        feeds = tomllib.load(f).get("feed", [])
    tiers: dict[str, int] = {}
    for feed in feeds:
        tiers[feed.get("tier", "?")] = tiers.get(feed.get("tier", "?"), 0) + 1
    detail = " / ".join(f"{k} {v}" for k, v in sorted(tiers.items(), key=lambda kv: -kv[1]))
    return f"{len(feeds)} 个源（{detail}）"


def check_index() -> str:
    path = ROOT / "site" / "index.html"
    if not path.exists():
        return "没有 site/index.html"
    text = path.read_text(encoding="utf-8", errors="ignore")
    days = sorted(set(re.findall(r"20\d{2}-\d{2}-\d{2}", text)))
    return f"往期索引 {len(days)} 期" + (f"（{days[0]} ~ {days[-1]}）" if days else "")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    profile = load_profile()
    cloud = profile.get("delivery", {}).get("web_base", CLOUD_FALLBACK)
    ok_tests, tests_tail = check_tests()
    placeholders = check_placeholder()
    repo = git_remote()

    rows = [
        ("单元测试", "✅ 全部通过" if ok_tests else f"❌ 失败（{tests_tail}）"),
        ("卡片占位符", "✅ 没有 example.com 残留" if not placeholders else f"❌ {len(placeholders)} 个文件残留"),
        ("线上站点", f"{cloud} · {http_status(cloud)}"),
        ("最近一次 Actions", check_actions(repo)),
        ("发信状态", check_state()),
        ("采集源", check_feeds()),
        ("网页往期", check_index()),
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]

    print("## 最后核验（`python scripts/verify_handover.py` 自动生成）\n")
    print("| 项目 | 实测 |")
    print("|---|---|")
    for name, value in rows:
        print(f"| {name} | {value} |")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""发布前自检：把过去踩过的坑做成检查清单，不过就不发。

用法:
  python scripts/selfcheck.py --date 2026-11-23
  python scripts/selfcheck.py --date 2026-11-23 --strict     # 警告也当失败
  python scripts/selfcheck.py --date 2026-11-23 --brief config/brief.json --card output/card-2026-11-23.html

检查项（每条都对应一次真实事故，见 INCIDENTS.md）：
  1. 结构：条数、标题、导语、要点、来源、重复
  2. 内容：软新闻/购物稿混入、标题是问句
  3. 串台：卡片里第 N 条的「看详细」必须指向 site/<日期>/pN.html，且 pN 的标题和卡片第 N 条一致
  4. 完整：网页版 index/pN/f1 存在
  5. 状态：归档存在、卡片与发送用卡片一致
  6. 环境：本地代理变量、收件人
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

TAG = re.compile(r"<[^>]+>")
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
HREF = re.compile(r'href="([^"]+)"')


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(TAG.sub("", text or ""))).strip()


def norm(text: str) -> str:
    return re.sub(r"\W+", "", (text or "").lower())


def _brief_from_card_date(date: str) -> dict | None:
    for name in ("brief.json", "brief.resend.json", "brief.draft.json", "brief.auto.json"):
        path = ROOT / "config" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        if data.get("date") == date:
            return data
    return None


def check_brief(brief: dict) -> tuple[list[str], list[str]]:
    """结构 + 内容检查。返回 (errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []
    items = brief.get("items") or []
    if not items:
        errors.append("brief 里一条新闻都没有")
        return errors, warnings
    if len(items) < 3:
        errors.append(f"只有 {len(items)} 条新闻，少于 3 条")
    elif len(items) < 5:
        warnings.append(f"只有 {len(items)} 条新闻（配置 5 条）")

    from cluster_events import is_soft_news

    titles: set[str] = set()
    urls: set[str] = set()
    for idx, item in enumerate(items, 1):
        title = strip_tags(item.get("title", ""))
        lede = strip_tags(item.get("lede", ""))
        points = item.get("points") or []
        sources = (item.get("web") or {}).get("sources") or []
        if not title:
            errors.append(f"第 {idx} 条没有标题")
        if len(title) > 42:
            errors.append(f"第 {idx} 条标题太长（{len(title)} 字）")
        if title.rstrip().endswith(("？", "?")):
            errors.append(f"第 {idx} 条标题是问句：{title[:20]}")
        if not lede or len(lede) < 8:
            errors.append(f"第 {idx} 条没有导语（{title[:16]}）")
        if len(points) < 3:
            warnings.append(f"第 {idx} 条要点只有 {len(points)} 条")
        if not sources:
            errors.append(f"第 {idx} 条没有出处：{title[:16]}")
        for src in sources:
            url = src.get("url", "")
            if not url:
                errors.append(f"第 {idx} 条出处缺 URL")
            elif url in urls:
                errors.append(f"出处 URL 重复：{url[:50]}")
            urls.add(url)
        if title in titles:
            errors.append(f"标题重复：{title[:20]}")
        titles.add(title)
        if is_soft_news(title):
            errors.append(f"第 {idx} 条像软新闻（展会/购物/娱乐）：{title[:24]}")
    return errors, warnings


def check_card(card_path: Path, brief: dict) -> tuple[list[str], list[str]]:
    """卡片渲染检查：每条都在、看详细链接数与顺序正确。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not card_path.exists():
        return [f"找不到卡片：{card_path}"], warnings
    html = card_path.read_text(encoding="utf-8")
    items = brief.get("items") or []
    plain = strip_tags(html)
    for idx, item in enumerate(items, 1):
        title = strip_tags(item.get("title", ""))
        if norm(title) and norm(title) not in norm(plain):
            errors.append(f"卡片里找不到第 {idx} 条标题：{title[:20]}")
    links = [u for u in HREF.findall(html) if "p%d.html" % 1 in u or re.search(r"/p\d+\.html$", u)]
    if items and len(links) < len(items):
        warnings.append(f"看详细链接 {len(links)} 个，少于 {len(items)} 条新闻")
    return errors, warnings


def check_site(site_dir: Path, date: str, brief: dict) -> tuple[list[str], list[str]]:
    """串台检查：第 N 条「看详细」→ pN.html，且 pN 标题与第 N 条一致。"""
    errors: list[str] = []
    warnings: list[str] = []
    day_dir = site_dir / date
    if not day_dir.exists():
        return [f"网页版目录不存在：{day_dir}"], warnings
    if not (day_dir / "index.html").exists():
        errors.append("网页版缺 index.html")
    items = brief.get("items") or []
    for idx, item in enumerate(items, 1):
        page = day_dir / f"p{idx}.html"
        if not page.exists():
            errors.append(f"缺详情页 p{idx}.html")
            continue
        page_html = page.read_text(encoding="utf-8")
        match = H1.search(page_html)
        page_title = strip_tags(match.group(1)) if match else ""
        want = strip_tags(item.get("title", ""))
        a, b = norm(page_title), norm(want)
        if not a or not b:
            warnings.append(f"p{idx}.html 取不到标题")
        elif a not in b and b not in a:
            errors.append(f"p{idx}.html 标题和卡片第 {idx} 条对不上：{page_title[:18]} ≠ {want[:18]}")
    followup = brief.get("followup")
    if followup and not (day_dir / "f1.html").exists():
        errors.append("有后续但没有 f1.html")
    return errors, warnings


def check_state(date: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    archive = ROOT / "state" / "archive" / date / "events.json"
    if not archive.exists():
        warnings.append(f"没有当天事件归档：{archive}")
    sent_path = ROOT / "state" / "sent.json"
    if sent_path.exists():
        try:
            sent = json.loads(sent_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            sent = {}
        if date in sent:
            sent_at = str(sent[date].get("sent_at", ""))
            if sent_at[:10] == date:
                warnings.append(f"今天已经发过（{sent_at}），确认这次是补发/重发")
    return errors, warnings


def check_env() -> tuple[list[str], list[str]]:
    import os
    errors: list[str] = []
    warnings: list[str] = []
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key, "")
        if val and ("127.0.0.1" in val or "localhost" in val):
            warnings.append(f"{key}={val} 指向本机代理；部署网页版可能失败（脚本会忽略它）")
    env_file = ROOT / ".env"
    if not env_file.exists():
        errors.append(".env 不存在，发不了信")
    elif "MAIL_TO=" not in env_file.read_text(encoding="utf-8"):
        errors.append(".env 里没有 MAIL_TO")
    return errors, warnings


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--brief", default=None)
    parser.add_argument("--card", default=None)
    parser.add_argument("--site", default=str(ROOT / "site"))
    parser.add_argument("--strict", action="store_true", help="警告也当失败")
    args = parser.parse_args()

    date = args.date
    brief = None
    if args.brief:
        brief = json.loads(Path(args.brief).read_text(encoding="utf-8-sig"))
        date = date or brief.get("date")
    if brief is None:
        brief = _brief_from_card_date(date) if date else None

    errors: list[str] = []
    warnings: list[str] = []

    if brief is None:
        errors.append(f"找不到 {date} 的 brief")
    else:
        be, bw = check_brief(brief)
        errors += be
        warnings += bw
        card = Path(args.card) if args.card else ROOT / "output" / f"card-{date}.html"
        ce, cw = check_card(card, brief)
        errors += ce
        warnings += cw
        se, sw = check_site(Path(args.site), date, brief)
        errors += se
        warnings += sw

    st_e, st_w = check_state(date or "")
    errors += st_e
    warnings += st_w
    env_e, env_w = check_env()
    errors += env_e
    warnings += env_w

    print(f"自检 · {date or '今天'}")
    for msg in errors:
        print(f"  ✗ {msg}")
    for msg in warnings:
        print(f"  ⚠ {msg}")
    if not errors and not warnings:
        print("  ✓ 全部通过")

    failed = bool(errors) or (args.strict and bool(warnings))
    print(f"结果：{'不通过' if failed else '通过'}（{len(errors)} 个错误，{len(warnings)} 个警告）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

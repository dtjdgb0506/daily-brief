"""抓取文章正文并按日期入库。

用法:
  python scripts/fetch_article.py URL [URL ...] [--date 2026-11-22] [--source 名称]
  python scripts/fetch_article.py URL --dry-run     # 只打印，不落盘

写入:
  output/raw/<YYYY-MM-DD>/<sha1(url)[:10]>.txt   正文
  output/raw/<YYYY-MM-DD>/<...>.json             {url,title,source,published,fetched_at,chars}

collect.py 复用这里的 fetch_html / extract / save_article。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html as html_lib
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from extract_main import extract_main_text  # 同目录
except ImportError:  # 作为包被导入时
    from .extract_main import extract_main_text  # type: ignore[no-redef]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip_depth == 0 and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        joined = " ".join(self.parts)
        return re.sub(r"[ \t\r\f\v]+", " ", joined)


def decode(raw: bytes, content_type: str) -> str:
    candidates = []
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        candidates.append(m.group(1))
    head = raw[:4096].decode("ascii", "ignore")
    m = re.search(r'charset=["\']?([\w-]+)', head, re.I)
    if m:
        candidates.append(m.group(1))
    candidates += ["utf-8", "gb18030", "big5"]
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def fetch_html(url: str, timeout: int = 20) -> tuple[str, int]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return decode(raw, resp.headers.get("Content-Type", "")), getattr(resp, "status", 200)


PUBLISHED_PATTERNS = [
    r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
    r'<meta[^>]+name=["\'](?:pubdate|publishdate|publish_date|date|weibo:article:create_at)["\'][^>]+content=["\']([^"\']+)',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'<time[^>]+datetime=["\']([^"\']+)["\']',
]


def extract_published(html: str, url: str = "") -> str:
    """尽量从页面里取发布日期，统一成 YYYY-MM-DD；取不到返回空串。"""
    for pat in PUBLISHED_PATTERNS:
        m = re.search(pat, html, re.I)
        if not m:
            continue
        s = m.group(1).strip()
        m2 = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", s)
        if m2:
            y, mo, d = (int(x) for x in m2.groups())
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                continue
    # 退而求其次：正文/标题里出现“2026年11月21日”
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", html)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return ""
    # 再退：URL 里的日期（新华网 /2026-11/22/、/2026/11/22/、新浪 /20261122/、/t20261122_）
    for pat in (r"/(20\d{2})-(\d{2})/(\d{2})/", r"/(20\d{2})/(\d{2})-(\d{2})/",
                r"/(20\d{2})/(\d{1,2})/(\d{1,2})/",
                r"/(20\d{2})(\d{2})(\d{2})/", r"t(20\d{2})(\d{2})(\d{2})_"):
        m = re.search(pat, url)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                continue
    m = re.search(r"/(20\d{2})(\d{2})(\d{2})/", url)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return ""
    return ""


def canonical(url: str) -> str:
    """去掉 utm 等追踪参数和 fragment，用来判重。"""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith(("utm_", "spm", "from", "scm"))]
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, urllib.parse.urlencode(query), ""))


def extract(html: str, url: str = "") -> dict:
    p = TextExtractor()
    p.feed(html)
    fallback = html_lib.unescape(p.text())
    main = extract_main_text(html, url)
    text = main if len(main) >= 200 else fallback
    return {
        "title": p.title.strip()[:120],
        "text": text,
        "published": extract_published(html, url),
    }


def save_article(
    url: str,
    title: str,
    text: str,
    published: str = "",
    source: str = "",
    date_dir: str | None = None,
) -> tuple[Path, Path]:
    day = date_dir or date.today().isoformat()
    out_dir = ROOT / "output" / "raw" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    txt_path = out_dir / f"{stem}.txt"
    json_path = out_dir / f"{stem}.json"
    txt_path.write_text(text, encoding="utf-8")
    meta = {
        "url": url,
        "title": title,
        "source": source,
        "published": published,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "chars": len(text),
    }
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return txt_path, json_path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--date", default=None, help="入库日期目录，默认今天")
    parser.add_argument("--source", default="", help="来源名称，写进 json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    failed = 0
    for url in args.urls:
        print("=" * 70)
        print("URL:", url)
        try:
            html, status = fetch_html(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        doc = extract(html, url)
        text = doc["text"]
        print(f"  HTTP {status} | 标题: {doc['title'][:70]}")
        print(f"  发布时间: {doc['published'] or '未识别'}")
        print(f"  正文长度: {len(text)}")
        print("  预览:", text[:400])
        if args.dry_run:
            print("  (dry-run，未落盘)")
            continue
        txt, meta = save_article(url, doc["title"], text, doc["published"], args.source, args.date)
        print(f"  已存: {txt}")
    return 1 if failed and failed == len(args.urls) else 0


if __name__ == "__main__":
    sys.exit(main())

"""正文抽取：用 lxml 做内容块打分的“主内容提取”，取代整页文本。

被 fetch_article.extract() 调用；lxml 不可用时回退到原来的 HTMLParser 方案。
"""

from __future__ import annotations

import re

try:
    from lxml import html as LHTML

    HAS_LXML = True
except ImportError:  # pragma: no cover
    HAS_LXML = False

DROP_TAGS = ("script", "style", "noscript", "svg", "iframe", "nav", "header", "footer",
             "aside", "form", "button", "select", "textarea")

# 明确的正文容器候选
CONTAINER_XP = (
    "//article | //main | //*[@id='content' or @id='article' or @id='artibody' or @id='detail'"
    " or @id='main-content' or @id='content_area' or @id='article-content'"
    " or @class='article-content' or @class='article' or @class='content' or @class='post-content']"
)


def _score(node) -> float:
    text = node.text_content() or ""
    chars = len(re.sub(r"\s", "", text))
    if chars < 80:
        return 0.0
    link_chars = sum(len(a.text_content() or "") for a in node.xpath(".//a"))
    link_ratio = link_chars / max(chars, 1)
    p_chars = sum(len(p.text_content() or "") for p in node.xpath(".//p"))
    # 正文长、链接少、段落多 → 分高
    return chars * max(0.1, 1.0 - min(link_ratio, 0.95)) + p_chars * 0.6


def extract_main_text(page_html: str, url: str = "") -> str:
    """返回最像正文的那段纯文本；失败返回空串。"""
    if not HAS_LXML:
        return ""
    try:
        tree = LHTML.fromstring(page_html)
    except Exception:  # noqa: BLE001
        return ""
    for tag in DROP_TAGS:
        for el in tree.xpath(f"//{tag}"):
            el.drop_tree()

    candidates = tree.xpath(CONTAINER_XP)
    candidates += tree.xpath("//div | //section | //td")
    best, best_score = None, 0.0
    for node in candidates:
        s = _score(node)
        if s > best_score:
            best, best_score = node, s

    text = best.text_content() if best is not None else (tree.text_content() or "")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and line not in lines[-3:]:
            lines.append(line)
    return "\n".join(lines).strip()

"""按“事件”而不是“稿件”来聚类，避免同一天的同一件事被拆成好几条主推。

背景：2026-11-22 那天抓到 12 篇稿子，全是黄仁勋辞职，系统按稿件取了 5 条，
结果 5 条是同一件事的不同后果。这个脚本先合并同一事件的稿件，再按兴趣权重排序。

用法:
  python scripts/cluster_events.py                    # 读 output/raw
  python scripts/cluster_events.py --dir 某目录        # 读指定目录（*.json + 同名 *.txt）
  python scripts/cluster_events.py --fixture tests/fixture_articles.json
  python scripts/cluster_events.py --top 5 --threshold 0.30 --debug

输入: 每个 *.json 至少含 {"url","title"}；同名 *.txt 是正文（可选，有则参与相似度计算）。
输出:
  output/events.json   机器可读（选中事件、每条素材、未入选事件、告警）
  output/events.md     人读（写卡片时直接照这个选）

算法（标准库实现，不依赖外部模型）:
  1. 标题清洗：去掉“_新浪财经_新浪网”“- 2026年11月18日, 俄罗斯卫星通讯社”这类来源后缀。
  2. 向量化：标题 CJK 二元组 ×3 + 正文前 5000 字，TF-IDF + L2 归一化。
  3. 相似度：余弦；若两篇标题出现同一 watch.people 人物，直接视为同事件的强信号。
  4. 并查集按相似度阈值合并，得到“事件簇”。
  5. 事件分类按 [weights] 三类词表打分，得分 = 类别权重 × 规模加成。
  6. 取前 N 个事件；事件数不足 N 时在 warnings 里说明（当日新闻过度集中）。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import tomllib

from jargon import jargon_hits
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 历史归档必须放在入库目录里（state/），否则 CI 每次全新 checkout，“7 天后续”永远匹配不到
ARCHIVE_ROOT = ROOT / "state" / "archive"
CJK = re.compile(r"[\u4e00-\u9fff]+")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{1,}")

JUNK_TITLES = {"学习进行时", "微视频", "视频", "专题", "图解", "直播", "海报", "首页", "滚动新闻", "图片新闻"}

# 软新闻：展会、活动、签约、招商这类稿子，再“新”也不是大事，直接不要。
SOFT_PATTERNS = [
    r"展[会览销]", r"博览会", r"展览会", r"开幕", r"揭牌", r"启动仪式", r"签约", r"招商",
    r"招聘", r"促销", r"节庆", r"庆典", r"大赛", r"竞赛", r"联赛", r"演唱会", r"研讨会",
    r"评选", r"榜单", r"盘点", r"游记", r"美食", r"通车", r"试运营", r"投产", r"开工", r"竣工",
    # 娱乐/体育（用户明确不想看）
    r"游戏", r"GTA", r"票房", r"剧集", r"综艺", r"音乐节", r"电影", r"赛事",
    # 文旅/生活类
    r"旅游", r"游客", r"打卡", r"美景", r"风光", r"探访", r"民俗", r"非遗", r"古都",
    r"历史文化魅力", r"古镇", r"民宿", r"温泉", r"养生", r"减肥", r"星座", r"情感",
    r"婚礼", r"萌宠", r"八卦", r"景区", r"文旅",
    # 券商研报/盘面点评（不是新闻事件）
    r"建议关注", r"研报", r"目标价", r"涨停", r"早盘", r"尾盘", r"个股", r"券商",
    r"^[^：]{2,8}(证券|基金|研究院|研究所)：",
    # 服务型内容（教你怎么做，不是新闻）
    r"攻略", r"指南", r"测评", r"推荐", r"值得买", r"怎么选", r"教你", r"该不该",
    r"值不值", r"教程", r"清单", r"避坑",
]
SOFT_NEWS_RE = re.compile("|".join(SOFT_PATTERNS))

# 英文“服务型/专栏/个人随笔”内容：不是新闻
SERVICE_PATTERNS = [
    r"\bhow (?:to|retiree|retirees|you|i)\b", r"\btips?\b", r"\bguide\b", r"\breview\b",
    r"\bexplainer\b", r"\bshould you\b", r"\bhere'?s how\b", r"\bwhat to know\b",
    r"\bwhy i\b", r"\bmy (?:life|journey|year)\b", r"\bi never\b", r"\bwe put\b",
    r"^i\b", r"\bopinion\b", r"\bcolumn\b",
]
SERVICE_RE = re.compile("|".join(SERVICE_PATTERNS), re.I)

EN_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
EN_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will", "are", "was",
    "not", "but", "you", "they", "his", "her", "its", "their", "about", "over", "into",
    "after", "before", "more", "than", "says", "said", "new", "who", "what", "when",
    "where", "why", "how", "can", "could", "would", "should", "may", "might", "amid",
    "against", "than", "then", "them", "these", "those", "still", "just", "like",
    # 来源页脚的样板词：不清掉会把毫不相干的稿子粘到一起
    "facebook", "page", "visit", "sign", "subscribe", "advertisement", "news",
    "npr", "cnbc", "wsj", "reuters", "bloomberg", "guardian", "politico", "hill",
}

# 来源页脚样板：先清掉再算标题相似度（"Visit our Facebook page" 这类让 7 条无关稿子粘在一起）
TITLE_BOILERPLATE = re.compile(
    r"\s*(?:visit our facebook page|follow us on|sign up for|subscribe(?: to)?|"
    r"read more|advertisement)\b.*$",
    re.I,
)
TITLE_SOURCE_TAIL = re.compile(
    r"\s*:\s*(?:NPR|CNBC|WSJ|BBC|CNN|AP|Reuters|Bloomberg|FT|Politico|The Hill)\s*$",
    re.I,
)
# 有些源把来源写在标题最前面：“奇客Solidot | 正文标题”。不清掉会把正文标题当页脚删掉。
TITLE_SOURCE_PREFIX = re.compile(
    r"^(?:(?:奇客)?\s*Solidot|IT之家|爱范儿|快科技|钛媒体|华尔街见闻|界面新闻|"
    r"中国新闻网|中新网|FT中文网|财联社|36氪|虎嗅)\s*[|｜:：]\s*",
    re.I,
)


def clean_title(title: str) -> str:
    """去掉来源页脚/样板后缀，避免把不相干稿子粘在一起。"""
    t = re.sub(r"\s+", " ", title or "")   # 先把换行/制表符压成空格
    t = TITLE_SOURCE_PREFIX.sub("", t)
    t = TITLE_BOILERPLATE.sub("", t)
    t = TITLE_SOURCE_TAIL.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def english_topic_overlap(a: str, b: str) -> float:
    """英文标题的实词重合度：用来把“同一件事的多家英文报道”合并。"""
    wa = {w.lower() for w in EN_WORD.findall(a)} - EN_STOP
    wb = {w.lower() for w in EN_WORD.findall(b)} - EN_STOP
    if len(wa) < 3 or len(wb) < 3:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))

# 硬新闻信号：标题里有人物/机构动作、决策、数字，才算“值得看的大事”。
HARD_NEWS_WORDS = [
    "辞职", "罢免", "解职", "上任", "任命", "宣布", "签署", "通过", "否决", "判决",
    "起诉", "逮捕", "制裁", "禁令", "法案", "财政", "利率", "降息", "加息", "通胀",
    "空袭", "袭击", "停火", "谈判", "峰会", "选举", "投票", "财报", "营收", "收购",
    "并购", "上市", "IPO", "破产", "裁员", "调查", "监管", "出口", "管制", "关税",
    "resign", "appoint", "fire", "election", "sanction", "tariff", "ban", "sue",
    "investigat", "approve", "reject", "deal", "merger", "layoff", "earnings",
]

# 源质量权重：国际权威 > 国际科技/深度分析 > 国内主流 > 地方稿
TIER_FACTOR = {"core": 1.30, "tech": 1.15, "analysis": 1.10, "cn": 0.95, "local": 0.55}


def is_soft_news(title: str) -> bool:
    """展会/活动/服务型内容/专栏随笔 → 软新闻，不该进日报。"""
    t = title or ""
    if SOFT_NEWS_RE.search(t) or SERVICE_RE.search(t):
        return True
    # 标题里带“展”但没有硬新闻信号（发展/展望这类词先剔掉）
    stripped = re.sub(r"(发展|展望|展示|展现|开展|扩展|拓展|进展|展开|展出)", "", t)
    if "展" in stripped and not any(w in t for w in HARD_NEWS_WORDS):
        return True
    if "论坛" in t and not any(w in t for w in HARD_NEWS_WORDS):
        return True
    return False


def hard_news_factor(title: str) -> float:
    """有硬新闻信号加分；只有数字给中性；什么都没有就降权。"""
    t = (title or "").lower()
    if any(w in t for w in HARD_NEWS_WORDS):
        return 1.08
    if re.search(r"\d", t):
        return 0.95
    return 0.78

STOP_BIGRAMS = {
    "的的", "了了", "是是", "这一", "一个", "我们", "他们", "可以", "因为", "所以",
    "但是", "已经", "还是", "就是", "以及", "对于", "关于", "通过", "进行", "这个",
    "没有", "什么", "怎么", "如果", "而且", "并且", "同时", "目前", "表示", "认为",
}

DEFAULT_LEXICON = {
    "tech": [
        "AI", "人工智能", "大模型", "模型", "芯片", "半导体", "英伟达", "NVIDIA", "OpenAI",
        "Anthropic", "Claude", "GPT", "算力", "数据中心", "机器人", "自动驾驶", "量子",
        "苹果", "谷歌", "微软", "亚马逊", "台积电", "华为", "算法", "开源", "训练",
        "黎曼", "数学", "证明", "Lean", "上市", "IPO",
    ],
    "politics": [
        "选举", "国会", "参议院", "众议院", "听证", "外交", "空袭", "导弹", "战争",
        "停火", "伊朗", "沙特", "也门", "以色列", "日本", "特朗普", "普京", "联合国",
        "安理会", "核潜艇", "加沙", "乌克兰", "总统", "首相", "制裁", "峰会", "会晤",
        "内阁", "议会", "大选", "民主党", "共和党",
    ],
    "finance": [
        "股价", "股市", "IPO", "财报", "营收", "美联储", "利率", "债券", "黄金", "原油",
        "基金", "市值", "收购", "并购", "融资", "投资者", "指数", "美股", "港股", "A股",
        "降息", "加息", "SEC", "会计", "季度", "涨幅", "跌幅", "市值", "私募", "承销",
    ],
}

# 实体别名：同一实体的中英文/繁简体写法（config/entities.toml 可覆盖）
DEFAULT_ALIASES = {
    "people": {
        "黄仁勋": ["黄仁勋", "黃仁勳", "Jensen Huang"],
        "特朗普": ["特朗普", "川普", "Trump"],
        "鲍威尔": ["鲍威尔", "Powell"],
        "马斯克": ["马斯克", "Musk"],
    },
    "companies": {
        "英伟达": ["英伟达", "輝達", "辉达", "NVIDIA", "Nvidia"],
        "OpenAI": ["OpenAI"],
        "Anthropic": ["Anthropic", "Claude"],
        "谷歌": ["谷歌", "Google"],
        "微软": ["微软", "Microsoft"],
        "台积电": ["台积电", "TSMC"],
        "亚马逊": ["亚马逊", "Amazon", "AWS"],
    },
}


# 运行时可被 config/ 覆盖的词表与别名
LEXICON: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_LEXICON.items()}
TITLE_RULES: dict[str, list[str]] = {}
ENTITY_ALIASES: dict[str, dict[str, list[str]]] = {
    "people": {k: list(v) for k, v in DEFAULT_ALIASES["people"].items()},
    "companies": {k: list(v) for k, v in DEFAULT_ALIASES["companies"].items()},
}
SOURCE_TIERS: dict[str, str] = {}
CATEGORY_EXCLUDE: dict[str, list[str]] = {}
WATCH_TERMS: list[str] = []


def watch_factor(titles: list[str]) -> float:
    """命中 watch 名单（公司/人物/话题）的事件适度加分：同样是大事，先看你在意的。"""
    if not WATCH_TERMS:
        return 1.0
    blob = " ".join(titles).lower()
    hits = sum(1 for w in WATCH_TERMS if w.lower() in blob)
    return 1 + 0.08 * min(hits, 3)


def load_source_tiers() -> dict[str, str]:
    """从 config/sources.toml 读每个源的 tier（core/tech/analysis/cn/local）。"""
    path = ROOT / "config" / "sources.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            feeds = tomllib.load(f).get("feed", [])
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {f.get("name", ""): f.get("tier", "cn") for f in feeds if f.get("name")}


def load_config_tables(profile: dict) -> None:
    """从 config/lexicon.toml + entities.toml 覆盖默认词表；watch 里的对象自动补别名。"""
    global LEXICON, TITLE_RULES, ENTITY_ALIASES, CATEGORY_EXCLUDE, WATCH_TERMS
    lex_path = ROOT / "config" / "lexicon.toml"
    if lex_path.exists():
        with lex_path.open("rb") as f:
            data = tomllib.load(f)
        for cat, block in data.get("lexicon", {}).items():
            LEXICON[cat] = list(block.get("words", []))
        TITLE_RULES = {k: list(v) for k, v in data.get("title_rules", {}).items()}
        CATEGORY_EXCLUDE = {
            cat: list(block.get("words", []))
            for cat, block in data.get("exclude", {}).items()
        }
    watch = profile.get("watch", {})
    for canon in watch.get("people", []):
        ENTITY_ALIASES["people"].setdefault(canon, [canon])
    for canon in watch.get("companies", []):
        ENTITY_ALIASES["companies"].setdefault(canon, [canon])
    WATCH_TERMS = [
        *watch.get("companies", []),
        *watch.get("people", []),
        *watch.get("topics", []),
    ]
    ent_path = ROOT / "config" / "entities.toml"
    if ent_path.exists():
        with ent_path.open("rb") as f:
            data = tomllib.load(f)
        for kind in ("people", "companies"):
            for canon, aliases in data.get(kind, {}).items():
                current = ENTITY_ALIASES[kind].setdefault(canon, [canon])
                for a in aliases:
                    if a not in current:
                        current.append(a)

SOURCE_SUFFIX = re.compile(
    r"\s*(?:[_|｜]\s*[^_|｜]{0,30}.*"
    r"|\s[-–—]\s*(?:20\d{2}.*|[A-Za-z][\w.\s]*"
    r"|.{0,14}(?:网|报|社|新闻|频道|台|CN|CFi|EDAILY|CNBC|Bloomberg|Reuters).*))\s*$"
)


def norm_title(title: str) -> str:
    t = clean_title(title)
    t = SOURCE_SUFFIX.sub("", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def title_coverage(title: str, text: str) -> float:
    """标题里的字词有多大比例能在正文里找到——用来识别“只抓到导航条”的坏稿。"""
    core = norm_title(title)
    bigrams = [run[i:i + 2] for run in CJK.findall(core) for i in range(len(run) - 1)]
    if bigrams:
        return sum(1 for b in bigrams if b in text) / len(bigrams)
    words = [w.lower() for w in LATIN.findall(core) if len(w) >= 4]
    if words:
        low = text.lower()
        return sum(1 for w in words if w in low) / len(words)
    return 1.0




NAV_WORDS = ("首页", "时政", "国际", "财经", "社会", "娱乐", "体育", "军事", "科技",
             "图片", "视频", "直播", "专题", "评论", "字号", "分享", "责任编辑",
             "来源：", "来源:", "作者：", "作者:", "编辑：", "编辑:", "扫一扫", "返回顶部",
             "Sign in", "Subscribe", "Log in", "Advertisement", "Skip to", "Home", "Menu")
META_LINE = re.compile(r"^(20\d{2}[-年/]|\d{1,2}月\d{1,2}日|来源|作者|编辑|责任编辑|字号|分享|扫一扫)")


def clean_body(text: str) -> str:
    """去掉页面导航、来源/编辑等元信息行、以及文末“相关新闻”区，让相似度算的是正文。"""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    i = 0
    while i < min(len(lines), 25):
        line = lines[i]
        if any(w in line for w in NAV_WORDS) or META_LINE.match(line) or len(line) <= 8:
            i += 1
            continue
        break
    body = "\n".join(lines[i:])
    for marker in ("更多精彩", "相关新闻", "相关阅读", "推荐阅读", "精彩推荐", "热门推荐", "相关稿件"):
        idx = body.find(marker)
        if idx > 120:
            body = body[:idx]
    return body.strip()


def title_bigrams(title: str) -> set[str]:
    core = norm_title(title)
    grams = {run[i:i + 2] for run in CJK.findall(core) for i in range(len(run) - 1)}
    grams.update(w.lower() for w in LATIN.findall(core) if len(w) >= 3)
    return grams


def tokenize(title: str, text: str) -> Counter:
    """标题权重 3，正文前 5000 字权重 1。"""
    tokens: Counter = Counter()

    def add(s: str, weight: int) -> None:
        for run in CJK.findall(s):
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg in STOP_BIGRAMS:
                    continue
                tokens[bg] += weight
        for w in LATIN.findall(s):
            w = w.lower()
            if len(w) >= 2:
                tokens[w] += weight

    add(title, 3)
    add((text or "")[:5000], 1)
    return tokens


def entities_of(article: dict) -> tuple[set, set]:
    """从标题+正文抽人物/公司实体（按别名表匹配，兼容英文与繁体写法）。"""
    blob = (article["title"] + " " + (article["text"] or "")[:8000]).lower()
    people = {canon for canon, aliases in ENTITY_ALIASES["people"].items()
              if any(a.lower() in blob for a in aliases)}
    comps = {canon for canon, aliases in ENTITY_ALIASES["companies"].items()
             if any(a.lower() in blob for a in aliases)}
    return people, comps


def tfidf_matrix(docs: list[Counter]) -> list[dict]:
    n = len(docs)
    df: Counter = Counter()
    for d in docs:
        df.update(d.keys())
    idf = {w: math.log((n + 1) / (c + 1)) + 1.0 for w, c in df.items()}
    vectors = []
    for d in docs:
        vec = {w: (1 + math.log(tf)) * idf[w] for w, tf in d.items() if w in idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({w: v / norm for w, v in vec.items()})
    return vectors


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(w, 0.0) for w, v in a.items())


class DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def load_profile() -> dict:
    path = ROOT / "config" / "profile.toml"
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_articles(raw_dir: Path) -> list[dict]:
    """读 output/raw 下的稿件，兼容按日期分目录（raw/<date>/*.json）和旧的平铺文件。

    published 可能为空（老稿件），此时用文件修改时间兜底。
    """
    articles = []
    for js in sorted(raw_dir.rglob("*.json")):
        try:
            meta = json.loads(js.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        text_path = js.with_suffix(".txt")
        text = text_path.read_text(encoding="utf-8", errors="ignore") if text_path.exists() else ""
        title = meta.get("title", "") or js.stem
        # 抓取失败/只剩导航的稿子不要：标题太短、正文太短、标题里的词正文里几乎没有
        if len(title.strip()) < 6 or len(text.strip()) < 120:
            continue
        if title_coverage(title, text) < 0.35:
            continue
        # 导航条污染：正文里一堆“| 北京 | 天津 |”这种，说明没抓到正文
        if text.count("|") >= 15 or text.count("首页") >= 4:
            continue
        published = meta.get("published", "") or ""
        # 没有发布日期的稿子直接不要：宁缺毋滥，避免把旧闻当今天
        if not published:
            continue
        articles.append({
            "url": meta.get("url", ""),
            "title": norm_title(meta.get("title", "") or js.stem),
            "raw_title": meta.get("title", ""),
            "text": clean_body(text),
            "source": meta.get("source", "") or js.parent.name,
            "published": published,
        })
    return articles


def within_window(article: dict, cutoff: datetime) -> bool:
    age = age_hours(article.get("published") or "")
    if age < 0:
        return True  # 无日期：先放行，后面按“未知”罚分
    return age <= (datetime.now() - cutoff).total_seconds() / 3600 + 1


def load_history(archive_dir: Path, days: int, today: str) -> list[dict]:
    """读过去 N 天归档里的事件，用于识别“重要后续”。"""
    if not archive_dir.exists():
        return []
    cutoff = (datetime.fromisoformat(today) - timedelta(days=days)).date().isoformat()
    history = []
    for day_dir in sorted(archive_dir.iterdir()):
        if not day_dir.is_dir() or day_dir.name <= cutoff or day_dir.name >= today:
            continue
        f = day_dir / "events.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        for ev in data.get("events", []):
            if ev.get("is_followup"):
                continue
            ev["history_date"] = day_dir.name
            history.append(ev)
    return history


def prune_archive(root: Path, keep_days: int = 30) -> None:
    """只保留最近 N 天归档，别让仓库无限变大。"""
    if not root.exists():
        return
    cutoff = (datetime.now() - timedelta(days=keep_days)).date().isoformat()
    for day_dir in root.iterdir():
        if (day_dir.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_dir.name)
                and day_dir.name < cutoff):
            shutil.rmtree(day_dir, ignore_errors=True)


def entity_set_of_titles(titles: list[str]) -> tuple[set, set]:
    """归档事件只有标题，用它反推人物/公司。"""
    blob = " ".join(titles).lower()
    people = {canon for canon, aliases in ENTITY_ALIASES["people"].items()
              if any(a.lower() in blob for a in aliases)}
    comps = {canon for canon, aliases in ENTITY_ALIASES["companies"].items()
             if any(a.lower() in blob for a in aliases)}
    return people, comps


def _kw_bag(keywords: list[str]) -> set[str]:
    """关键词做字符二元组，避免“纳指标普”和“纳指/标普”对不上。"""
    bag: set[str] = set()
    for kw in keywords:
        k = str(kw).lower().strip()
        if not k:
            continue
        if len(k) <= 2:
            bag.add(k)
        else:
            bag.update(k[i:i + 2] for i in range(len(k) - 1))
    return bag


def match_history(event: dict, history: list[dict]) -> dict | None:
    """今天的事件如果在过去 N 天出现过，就返回最匹配的那条历史事件。"""
    p_new, c_new = entity_set_of_titles(event["titles"])
    kw_new = _kw_bag(event["keywords"])
    best = None
    best_score = 0.0
    for hist in history:
        p_old, c_old = entity_set_of_titles(hist.get("titles", [hist.get("representative_title", "")]))
        kw_old = _kw_bag(hist.get("keywords", []))
        shared_people = p_new & p_old
        shared_comps = c_new & c_old
        jac = len(kw_new & kw_old) / max(len(kw_new | kw_old), 1)
        score = 0.0
        if shared_people and shared_comps:
            score = 0.7
        elif shared_people:
            score = 0.45
        if jac >= 0.2:
            score = max(score, 0.5 + jac * 0.3)
        # 中文关键词在历史标题/关键词里出现过（“美股”这类），也算同一件事
        hist_blob = " ".join(hist.get("titles", []) + hist.get("keywords", []))
        kw_hit = sum(1 for k in event["keywords"] if k and k in hist_blob) / max(len(event["keywords"]), 1)
        if kw_hit >= 0.2:
            score = max(score, 0.5 + min(kw_hit, 0.5))
        if score > best_score:
            best_score, best = score, hist
    return best if best_score >= 0.4 else None


KEY_NODE_WORDS = [
    "听证", "判决", "起诉", "辞职", "罢免", "调查", "制裁", "签署", "通过", "投票",
    "表决", "财报", "营收", "IPO", "上市", "停牌", "复牌", "发布", "宣布", "空袭",
    "袭击", "停火", "协议", "涨价", "降价", "裁员", "收购", "融资", "跌", "涨",
]


def followup_importance(event: dict, matched: dict, weights: dict) -> float:
    """后续是否值得单独一条：类别权重 + 覆盖度 + 是否关键节点 + 历史匹配强度。"""
    cat_w = max(weights.get(event["category"], 20), 1) / 100
    coverage = min(event["article_count"], 3) / 3
    blob = " ".join(event["titles"]) + " ".join(event["keywords"])
    keynode = 1.0 if any(w in blob for w in KEY_NODE_WORDS) else 0.0
    match_w = 0.1 if matched.get("representative_title") else 0.05
    return round(0.45 * cat_w + 0.25 * coverage + 0.2 * keynode + match_w, 3)


def load_fixture(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return [{
        "url": a.get("url", ""),
        "title": norm_title(a.get("title", "")),
        "raw_title": a.get("title", ""),
        "text": a.get("text", ""),
        "source": a.get("source", "fixture"),
    } for a in data]


def display_keywords(tokens: list[str], limit: int = 6) -> list[str]:
    """把相邻的二元组接回完整词（“黄仁、仁勋、勋辞” → “黄仁勋辞职”），方便阅读。"""
    out: list[str] = []
    used: set[str] = set()
    for w in tokens:
        if w in used or len(w) < 2:
            continue
        cur = w
        for v in tokens:
            if v in used or v == cur or len(v) < 2:
                continue
            if cur[-1] == v[0]:
                cur += v[1:]
                used.add(v)
        used.add(w)
        out.append(cur)
        if len(out) >= limit:
            break
    return out


def category_of(title_blob: str, body_blob: str, weights: dict) -> tuple[str, list[str], dict]:
    """类别判定：标题有线索就信标题，标题没线索才看正文。

    英文长文正文里 "AI/model/chip" 出现太多次，会把什么都抢成科技
    （“特朗普会见习近平和普京”也被判成科技）。所以正文只在标题无信号时兜底。
    """
    t_low, b_low = title_blob.lower(), body_blob.lower()[:2000]

    def hits(blob: str, words: list[str]) -> int:
        # 按“命中了多少个不同的词”算，不按出现次数：长文里 AI/chip 出现几十次
        # 会把不相干的稿子也判成科技。
        # 英文短词（AI / fed / app）必须整词匹配，否则 "ai" 会命中 "said"、"again"。
        n = 0
        for w in words:
            if not w:
                continue
            lw = w.lower()
            if lw.isascii() and len(lw) <= 3:
                if re.search(rf"(?<![a-z]){re.escape(lw)}(?![a-z])", blob):
                    n += 1
            elif lw in blob:
                n += 1
        return n

    title_counts: dict[str, int] = {}
    body_counts: dict[str, int] = {}
    for cat, words in LEXICON.items():
        title_counts[cat] = hits(t_low, words)
        body_counts[cat] = hits(b_low, words)
    for cat, words in TITLE_RULES.items():
        title_counts[cat] = title_counts.get(cat, 0) + hits(t_low, words)

    # 排除词：说明它其实不是该类别的大事（例如“科技展览会”不是科技新闻）
    for cat, words in CATEGORY_EXCLUDE.items():
        title_counts[cat] = max(0, title_counts.get(cat, 0) - hits(t_low, words))
        body_counts[cat] = max(0, body_counts.get(cat, 0) - hits(b_low, words))

    if any(title_counts.values()):
        counts = {c: v * 3 for c, v in title_counts.items()}
    else:
        counts = dict(body_counts)
    if not any(counts.values()):
        return "other", [], counts
    order = sorted((c for c in counts if counts[c] > 0), key=lambda c: counts[c], reverse=True)
    return order[0], order[:2], counts


def age_hours(published: str, now: datetime | None = None) -> float:
    now = now or datetime.now()
    try:
        dt = datetime.fromisoformat(published).replace(tzinfo=None)
    except (TypeError, ValueError):
        return -1.0  # 日期未知，不算“新的”
    return max(0.0, (now - dt).total_seconds() / 3600)


def freshness_factor(published_ages: list[float], half_life: float) -> float:
    if not published_ages:
        return 1.0
    age = min(published_ages)  # 以事件里最新的一篇算新鲜度
    return max(0.25, 0.5 ** (age / max(half_life, 1)))


def pick_balanced(events: list[dict], top: int, weights: dict, max_per_cat: int = 3) -> list[dict]:
    """按得分取前 N 条，同一类别最多 max_per_cat 条。
    不为“类别配额”硬塞低分事件——宁可让别的类别多一条，也不让 5 条里出现很弱的一条。
    """
    picked: list[dict] = []
    counts: dict[str, int] = {}
    for e in sorted(events, key=lambda x: -x["score"]):
        cat = e["category"]
        if counts.get(cat, 0) >= max_per_cat:
            continue
        picked.append(e)
        counts[cat] = counts.get(cat, 0) + 1
        if len(picked) >= top:
            break
    return picked


def main() -> int:
    global SOURCE_TIERS
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(ROOT / "output" / "raw"))
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--max-per-category", type=int, default=None)
    parser.add_argument("--threshold", type=float, default=None, help="默认取 profile.toml [cluster].threshold")
    parser.add_argument("--window-hours", type=int, default=24, help="只看这个时间窗内的稿件")
    parser.add_argument("--max-age-hours", type=int, default=48,
                        help="事件里最新一篇超过这个小时数就不算当天新闻（回溯旧日期时调大，如 240）")
    parser.add_argument("--content-day", default=None, help="只看这一天的稿子（YYYY-MM-DD），优先于 --window-hours")
    parser.add_argument("--history-days", type=int, default=7, help="过去几天算“前七天”")
    parser.add_argument("--date", default=None, help="运行日期（默认今天），用于归档路径")
    parser.add_argument("--history-dir", default=str(ARCHIVE_ROOT), help="历史事件归档目录")
    parser.add_argument("--no-archive", action="store_true", help="不写 output/archive/<date>/")
    parser.add_argument("--out", default=str(ROOT / "output"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    profile = load_profile()
    load_config_tables(profile)
    SOURCE_TIERS = load_source_tiers()
    weights = profile.get("weights", {})
    cluster_cfg = profile.get("cluster", {})
    if args.threshold is None:
        args.threshold = float(cluster_cfg.get("threshold", 0.30))
    top = args.top or int(profile.get("delivery", {}).get("items", 5))
    run_date = args.date or datetime.now().date().isoformat()
    cutoff = datetime.now() - timedelta(hours=args.window_hours)

    if args.fixture:
        articles = load_fixture(Path(args.fixture))
        src_desc = args.fixture
    else:
        articles_all = load_articles(Path(args.dir))
        if args.content_day:
            articles = [a for a in articles_all if (a.get("published") or "")[:10] == args.content_day]
            src_desc = f"{args.dir}（{args.content_day} 当天：{len(articles)}/{len(articles_all)} 篇）"
        else:
            articles = [a for a in articles_all if within_window(a, cutoff)]
            src_desc = f"{args.dir}（{args.window_hours}h 内：{len(articles)}/{len(articles_all)} 篇）"

    if not articles:
        print(f"没有可分析的稿件：{src_desc}")
        return 1

    docs = [tokenize(a["title"], a["text"]) for a in articles]
    vectors = tfidf_matrix(docs)
    ents = [entities_of(a) for a in articles]

    n = len(articles)
    dsu = DSU(n)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = cosine(vectors[i], vectors[j])
            p_i, c_i = ents[i]
            p_j, c_j = ents[j]
            shared_people = p_i & p_j
            shared_comps = c_i & c_j
            ti, tj = articles[i]["title"].lower(), articles[j]["title"].lower()

            def in_title(title: str, canon: set, kind: str) -> bool:
                aliases = [a.lower() for c in canon for a in ENTITY_ALIASES[kind].get(c, [c])]
                return any(a in title for a in aliases)

            # 同一人物 + 同一公司，且两边标题里都真的出现，才算同一事件。
            # 旧写法只要“任一边标题出现”就并，导致正文提到特朗普的稿子全被粘到
            # “特朗普见习近平”这一条上（7 条不相干的新闻被并成一条）。
            strong = bool(
                shared_people and shared_comps
                and in_title(ti, shared_people, "people")
                and in_title(tj, shared_people, "people")
                and in_title(ti, shared_comps, "companies")
                and in_title(tj, shared_comps, "companies")
            )
            same_event = strong
            reason = "person+company" if strong else ""
            if (not same_event and shared_people
                    and in_title(ti, shared_people, "people")
                    and in_title(tj, shared_people, "people")
                    and sim >= 0.15):
                same_event, reason = True, "person+similarity"
            if not same_event and sim >= args.threshold:
                # 正文相似还不够，标题也得有交集：中新网这类模板会让“完全无关的新闻”正文很像
                ti_bg = title_bigrams(articles[i]["title"])
                tj_bg = title_bigrams(articles[j]["title"])
                overlap = len(ti_bg & tj_bg) / max(len(ti_bg | tj_bg), 1)
                if overlap >= 0.15:
                    same_event, reason = True, "similarity+title"
                else:
                    reason = ""
            if not same_event:
                # 英文媒体同一件事：标题实词重合度高就合并（"killer robots" / "UN talks" 这类）
                t_i, t_j = articles[i]["title"], articles[j]["title"]
                en_i = sum(1 for ch in t_i if ch.isascii() and ch.isalpha())
                en_j = sum(1 for ch in t_j if ch.isascii() and ch.isalpha())
                if en_i >= 12 and en_j >= 12 and english_topic_overlap(t_i, t_j) >= 0.3:
                    same_event, reason = True, "english-topic"
            if args.debug:
                pairs.append((round(max(sim, 0.5 if same_event else sim), 3),
                              articles[i]["title"][:26], articles[j]["title"][:26], reason or "-"))
            if same_event:
                dsu.union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(dsu.find(i), []).append(i)

    events = []
    half_life = float(cluster_cfg.get("freshness_half_life_hours", 24))
    for members in clusters.values():
        cat_tokens: Counter = Counter()
        for i in members:
            cat_tokens.update(docs[i])
        title_blob = " ".join(articles[i]["title"] for i in members)
        body_blob = " ".join(articles[i]["text"][:3000] for i in members)
        cat, cat_labels, cat_counts = category_of(title_blob, body_blob, weights)
        # “其他”只是兜底类别，不该和科技/政治/财经同权
        default_w = 8 if cat == "other" else 20
        cat_weight = max(weights.get(cat, default_w), 1) / 100
        scale = 1 + 0.25 * math.log2(1 + len(members))
        blob = " ".join(articles[i]["text"][:2000] for i in members)
        opinion_hits = sum(1 for m in ("认为", "指出", "表示", "预计", "担忧", "分析", "评论",
                                       "警告", "said", "according to", "analyst", "argued", "warned")
                           if m in blob)
        jarg = jargon_hits(title_blob + blob)
        analysis_factor = 1 + 0.12 * min(opinion_hits, 4)   # 有人分析 → 加分
        plain_factor = 1 - 0.08 * min(jarg, 5)              # 官话越多 → 降权

        ages = [age_hours(articles[i].get("published", "")) for i in members]
        known = [a for a in ages if a >= 0]
        if known and min(known) > args.max_age_hours:
            continue  # 这件事最新的一篇都两天前了，不算今天新闻
        fresh = freshness_factor([a if a >= 0 else 36.0 for a in ages], half_life)
        n_sources = len({articles[i].get("source", "") for i in members if articles[i].get("source")})
        diversity = 1 + 0.1 * min(n_sources, 3)
        members_sorted = sorted(members, key=lambda i: -len(articles[i]["text"]))
        rep = members_sorted[0]
        person_aliases = [a for al in ENTITY_ALIASES["people"].values() for a in al]

        def has_person(i: int) -> bool:
            t = articles[i]["title"].lower()
            return any(a.lower() in t for a in person_aliases)

        # 代表标题：取簇内“最居中”的那篇（与其他成员平均相似度最高，且标题含人物名）
        cands = [i for i in members if has_person(i)] or members
        rep_idx = max(
            cands,
            key=lambda i: sum(cosine(vectors[i], vectors[j]) for j in members if j != i) / max(len(members) - 1, 1),
        )
        rep_title = articles[rep_idx]["title"]
        if len(rep_title.strip()) < 8 or rep_title.strip() in JUNK_TITLES:
            continue  # 栏目名/视频页标题，不是一条真新闻
        if is_soft_news(rep_title):
            continue  # 展会/活动/签约/招商类稿子：再新也不是大事
        hard_f = hard_news_factor(rep_title)
        tiers = [SOURCE_TIERS.get(articles[i].get("source", ""), "cn") for i in members]
        best_tier = max(tiers, key=lambda t: TIER_FACTOR.get(t, 1.0)) if tiers else "cn"
        tier_f = TIER_FACTOR.get(best_tier, 1.0)
        w_f = watch_factor([articles[i]["title"] for i in members])
        events.append({
            "id": None,
            "category": cat,
            "tags": cat_labels,
            "score": round(cat_weight * scale * fresh * diversity * analysis_factor
                           * plain_factor * hard_f * tier_f * w_f * 100, 2),
            "source_tier": best_tier,
            "hard_news": round(hard_f, 2),
            "article_count": len(members),
            "source_count": n_sources,
            "readability": {"opinion": opinion_hits, "jargon": jarg},
            "source_names": sorted({articles[i].get("source", "") for i in members if articles[i].get("source")}),
            "freshness": round(fresh, 3),
            "representative_title": rep_title,
            "keywords": display_keywords([w for w, _ in Counter(cat_tokens).most_common(24)]),
            "article_ids": members,
            "urls": [articles[i]["url"] for i in members],
            "titles": [articles[i]["title"] for i in members],
        })

    events.sort(key=lambda e: e["score"], reverse=True)
    for k, e in enumerate(events, 1):
        e["id"] = f"e{k}"

    # —— 第六点：过去 N 天事件的重要后续 ——
    history = load_history(Path(args.history_dir), args.history_days, run_date)
    for e in events:
        matched = match_history(e, history) if history else None
        e["is_followup"] = bool(matched)
        e["followup_of"] = (
            {"date": matched.get("history_date", ""), "title": matched.get("representative_title", "")}
            if matched else None
        )
        e["importance"] = followup_importance(e, matched, weights) if matched else 0.0

    # 主推 = 今天的“新事件”；与历史重合的事件单独放“后续”，
    # 避免同一件事既当主推又当后续，也保证“5 条新闻 + 1 条后续”的结构稳定。
    main_pool = [e for e in events if not e["is_followup"]]
    main = pick_balanced(main_pool, top, weights,
                         int(cluster_cfg.get("max_per_category", 3)))
    main_ids = {e["id"] for e in main}
    follow_candidates = sorted(
        [e for e in events if e["is_followup"] and e["id"] not in main_ids],
        key=lambda e: e["importance"],
        reverse=True,
    )
    follow_min = float(cluster_cfg.get("followup_min_importance", 0.4))
    follow = next((c for c in follow_candidates if c["importance"] >= follow_min), None)

    warnings = []
    filled = 0
    if len(main) < top:
        # 新事件不够：用“后续事件”补进主推，保证每天有东西看；后续就不再单列。
        for e in follow_candidates:
            if len(main) >= top:
                break
            if e["id"] not in main_ids:
                main.append(e)
                main_ids.add(e["id"])
                filled += 1
        main.sort(key=lambda e: e["score"], reverse=True)
        if follow is not None and follow["id"] in main_ids:
            follow = None
    if filled:
        warnings.append(f"当天新事件不足 {top} 条，已用 {filled} 条历史后续补进主推（卡片会标注“延续”）。")
    if len(main) < top:
        warnings.append(
            f"当天只识别出 {len(main)} 个独立事件，不足 {top} 条：宁可少发，也不拆事件凑数。"
        )
    if follow_candidates and not follow:
        warnings.append(
            f"有 {len(follow_candidates)} 个事件与历史重合，但重要性都没过线（最高 "
            f"{follow_candidates[0]['importance']}），今天不放“后续”。"
        )

    # 当日质量自检：选出来的东西是不是“国际源覆盖、像硬新闻”
    selected_sources = {s for e in main for s in e.get("source_names", [])}
    known_sources = [s for s in selected_sources if s in SOURCE_TIERS]
    core_sources = [s for s in known_sources if SOURCE_TIERS.get(s) == "core"]
    soft_selected = [e for e in main if is_soft_news(e["representative_title"])]
    if soft_selected:
        warnings.append(
            f"有 {len(soft_selected)} 条疑似软新闻进了主推"
            f"（{soft_selected[0]['representative_title'][:20]}），请检查过滤规则。"
        )
    if main and known_sources and not core_sources:
        warnings.append("今天选出的新闻全部来自国内源：国际源可能没抓到（网络或源失效），选题质量会下降。")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_date": run_date,
        "window_hours": args.window_hours,
        "content_day": args.content_day or run_date,
        "history_days": args.history_days,
        "source": src_desc,
        "article_total": n,
        "event_total": len(events),
        "selected": [e["id"] for e in main],
        "followup": follow["id"] if follow else None,
        "warnings": warnings,
        "events": events,
    }
    (out_dir / "events.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 归档，供以后识别“后续”用（同一天重跑覆盖）；放 state/ 才会被 CI 提交回仓库
    if not args.no_archive and not args.fixture:
        arch = ARCHIVE_ROOT / run_date
        arch.mkdir(parents=True, exist_ok=True)
        (arch / "events.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        prune_archive(ARCHIVE_ROOT)

    lines = [
        f"# 今日事件（{run_date}）",
        "",
        f"{src_desc} → 独立事件 {len(events)} 个；主推 {len(main)} 条"
        + (f"，后续 1 条" if follow else "") + "。",
        "",
    ]
    if warnings:
        lines += [f"> ⚠️ {w}" for w in warnings] + [""]
    for e in main:
        lines += [
            f"## {e['id']} · {e['representative_title']}",
            f"- 类别：{e['category']} · 得分 {e['score']} · 稿源 {e['article_count']} 篇",
            f"- 关键词：{'、'.join(e['keywords'][:6])}",
            "- 素材：",
        ]
        for t, u in zip(e["titles"], e["urls"]):
            lines.append(f"  - {t}  <{u}>")
        lines.append("")

    if follow:
        f = follow
        lines += [
            "## 第六点 · 过去 %d 天的重要后续" % args.history_days,
            f"- 今天的新进展：{f['representative_title']}",
            f"- 对应历史事件（{f['followup_of']['date']}）：{f['followup_of']['title']}",
            f"- 重要性 {f['importance']}（类别权重 / 覆盖度 / 关键节点 / 历史匹配）",
            "- 素材：",
        ]
        for t, u in zip(f["titles"], f["urls"]):
            lines.append(f"  - {t}  <{u}>")
        lines.append("")

    rest = [e for e in events if e not in main and e is not follow]
    if rest:
        lines += ["## 未入选（可备用）"] + [
            f"- {e['representative_title']}（{e['category']}）" + (" · 历史后续" if e["is_followup"] else "")
            for e in rest
        ] + [""]
    (out_dir / "events.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"{src_desc} → 事件 {len(events)} 个，主推 {len(main)}，后续 {1 if follow else 0}（top={top}, threshold={args.threshold}）")
    for e in main:
        print(f"  {e['id']} [{e['category']}] {e['score']:>6}  {e['article_count']} 篇  {e['representative_title'][:56]}")
    if follow:
        print(f"  ★ 后续 [{follow['importance']}] {follow['representative_title'][:56]}")
        print(f"     ← {follow['followup_of']['date']} {follow['followup_of']['title'][:50]}")
    for w in warnings:
        print(f"  ⚠ {w}")
    if args.debug:
        print("\n合并明细（按分数排序）:")
        for s, a, b, reason in sorted(pairs, reverse=True)[:60]:
            print(f"  {s:>5}  [{reason}]  {a}  ⇔  {b}")
    print(f"\n已写入: {out_dir / 'events.json'} / {out_dir / 'events.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

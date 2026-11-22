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
import sys
import tomllib
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CJK = re.compile(r"[\u4e00-\u9fff]+")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{1,}")

JUNK_TITLES = {"学习进行时", "微视频", "视频", "专题", "图解", "直播", "海报", "首页", "滚动新闻", "图片新闻"}

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


def load_config_tables(profile: dict) -> None:
    """从 config/lexicon.toml + entities.toml 覆盖默认词表；watch 里的对象自动补别名。"""
    global LEXICON, TITLE_RULES, ENTITY_ALIASES
    lex_path = ROOT / "config" / "lexicon.toml"
    if lex_path.exists():
        with lex_path.open("rb") as f:
            data = tomllib.load(f)
        for cat, block in data.get("lexicon", {}).items():
            LEXICON[cat] = list(block.get("words", []))
        TITLE_RULES = {k: list(v) for k, v in data.get("title_rules", {}).items()}
    watch = profile.get("watch", {})
    for canon in watch.get("people", []):
        ENTITY_ALIASES["people"].setdefault(canon, [canon])
    for canon in watch.get("companies", []):
        ENTITY_ALIASES["companies"].setdefault(canon, [canon])
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
    t = (title or "").strip()
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
        if not published:
            try:
                published = datetime.fromtimestamp(js.stat().st_mtime).isoformat(timespec="seconds")
            except OSError:
                published = ""
        articles.append({
            "url": meta.get("url", ""),
            "title": norm_title(meta.get("title", "") or js.stem),
            "raw_title": meta.get("title", ""),
            "text": text,
            "source": meta.get("source", "") or js.parent.name,
            "published": published,
        })
    return articles


def within_window(article: dict, cutoff: datetime) -> bool:
    pub = article.get("published") or ""
    try:
        return datetime.fromisoformat(pub).replace(tzinfo=None) >= cutoff.replace(tzinfo=None)
    except (TypeError, ValueError):
        return True


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
    coverage = min(event["article_count"], 5) / 5
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
    """一个事件可以有多个类别标签，排序时按权重最高的那个算。

    标题命中给 3 分、正文给 1 分；[title_rules] 里的动作词命中标题额外 +3。
    直接数词在原文里出现的次数（而不是 bigram 词表），避免“英伟达”这种 3 字词永远匹配不上。
    """
    t_low, b_low = title_blob.lower(), body_blob.lower()
    counts: dict[str, int] = {}
    for cat, words in LEXICON.items():
        counts[cat] = sum(t_low.count(w.lower()) * 3 + b_low.count(w.lower()) for w in words)
    for cat, words in TITLE_RULES.items():
        hits = sum(1 for w in words if w.lower() in t_low)
        counts[cat] = counts.get(cat, 0) + hits * 3
    if not any(counts.values()):
        return "other", [], counts
    # 类别只按“证据多少”判断（权重只用于排序打分，否则 tech 权重 50 会让一切归科技）
    order = sorted((c for c in counts if counts[c] > 0), key=lambda c: counts[c], reverse=True)
    return order[0], order[:2], counts


def age_hours(published: str, now: datetime | None = None) -> float:
    now = now or datetime.now()
    try:
        dt = datetime.fromisoformat(published).replace(tzinfo=None)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 3600)


def freshness_factor(published_ages: list[float], half_life: float) -> float:
    if not published_ages:
        return 1.0
    age = min(published_ages)  # 以事件里最新的一篇算新鲜度
    return max(0.6, 0.5 ** (age / max(half_life, 1)))


def pick_balanced(events: list[dict], top: int, weights: dict) -> list[dict]:
    """名额按“当天实际有哪些类别”动态分配：没有科技就多给政治/财经，
    每个类别内部按得分取，不会为了凑数把同一件事拆开。"""
    by_cat: dict[str, list[dict]] = {}
    for e in events:
        by_cat.setdefault(e["category"], []).append(e)
    cats = [c for c in by_cat if by_cat[c]]
    if not cats:
        return []
    def wgt(c: str) -> int:
        # “综合/其他”给最低权重：只在没有科技/政治/财经可用时才进来
        return 1 if c == "other" else max(weights.get(c, 10), 1)

    total_w = sum(wgt(c) for c in cats)
    raw = {c: top * wgt(c) / total_w for c in cats}
    alloc = {c: min(int(raw[c]), len(by_cat[c])) for c in cats}
    used = sum(alloc.values())

    # 余下名额：按“小数部分”从大到小补，且不超过该类别实际条数
    frac_order = sorted(cats, key=lambda c: (raw[c] - int(raw[c]), weights.get(c, 10)), reverse=True)
    while used < top:
        progressed = False
        for c in frac_order:
            if alloc[c] < len(by_cat[c]) and used < top:
                alloc[c] += 1
                used += 1
                progressed = True
        if not progressed:
            break

    picked: list[dict] = []
    for c in cats:
        picked.extend(by_cat[c][: alloc[c]])
    if len(picked) < top:
        rest = [e for e in events if e not in picked]
        picked.extend(rest[: top - len(picked)])
    picked.sort(key=lambda e: e["score"], reverse=True)
    return picked


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(ROOT / "output" / "raw"))
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--max-per-category", type=int, default=None)
    parser.add_argument("--threshold", type=float, default=None, help="默认取 profile.toml [cluster].threshold")
    parser.add_argument("--window-hours", type=int, default=24, help="只看这个时间窗内的稿件")
    parser.add_argument("--history-days", type=int, default=7, help="过去几天算“前七天”")
    parser.add_argument("--date", default=None, help="运行日期（默认今天），用于归档路径")
    parser.add_argument("--history-dir", default=str(ROOT / "output" / "archive"), help="历史事件归档目录")
    parser.add_argument("--no-archive", action="store_true", help="不写 output/archive/<date>/")
    parser.add_argument("--out", default=str(ROOT / "output"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    profile = load_profile()
    load_config_tables(profile)
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

            def in_titles(canon: set, kind: str) -> bool:
                aliases = [a.lower() for c in canon for a in ENTITY_ALIASES[kind].get(c, [c])]
                return any(a in ti or a in tj for a in aliases)

            # 同一人物 + 同一公司，且其中一个真的出现在标题里才算同一事件
            # （否则“顺带提到”会把不相干的稿子粘起来：例如特斯拉 FSD 提到英伟达）
            strong = bool(
                shared_people and shared_comps
                and in_titles(shared_people, "people")
                and in_titles(shared_comps, "companies")
            )
            same_event = strong
            reason = "person+company" if strong else ""
            if not same_event and shared_people and in_titles(shared_people, "people") and sim >= 0.15:
                same_event, reason = True, "person+similarity"
            if not same_event and sim >= args.threshold:
                same_event, reason = True, "similarity"
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
        cat_weight = max(weights.get(cat, 20), 1) / 100
        scale = 1 + 0.25 * math.log2(1 + len(members))
        ages = [age_hours(articles[i].get("published", "")) for i in members]
        fresh = freshness_factor(ages, half_life)
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
        events.append({
            "id": None,
            "category": cat,
            "tags": cat_labels,
            "score": round(cat_weight * scale * fresh * diversity * 100, 2),
            "article_count": len(members),
            "source_count": n_sources,
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

    # 主推永远是今天得分最高的 N 条（哪怕它本身是某个旧故事的延续）；
    # 第六点从“没挤进主推、但和前七天事件对得上”的事件里挑一条最重要的。
    main = pick_balanced(events, top, weights)
    main_ids = {e["id"] for e in main}
    follow_candidates = sorted(
        [e for e in events if e["is_followup"] and e["id"] not in main_ids],
        key=lambda e: e["importance"],
        reverse=True,
    )
    follow_min = float(cluster_cfg.get("followup_min_importance", 0.4))
    follow = next((c for c in follow_candidates if c["importance"] >= follow_min), None)

    warnings = []
    if len(main) < top:
        warnings.append(
            f"当天只识别出 {len(main)} 个新的独立事件，不足 {top} 条：新闻高度集中于同一事件，"
            f"应按事件而非稿件凑数（宁可发 {len(main)} 条，也不要把一件事拆成多条）。"
        )
    if follow_candidates and not follow:
        warnings.append(
            f"有 {len(follow_candidates)} 个事件与历史重合，但重要性都没过线（最高 "
            f"{follow_candidates[0]['importance']}），今天不放“后续”。"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_date": run_date,
        "window_hours": args.window_hours,
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

    # 归档，供以后识别“后续”用（同一天重跑覆盖）
    if not args.no_archive and not args.fixture:
        arch = out_dir / "archive" / run_date
        arch.mkdir(parents=True, exist_ok=True)
        (arch / "events.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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

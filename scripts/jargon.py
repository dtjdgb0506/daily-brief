"""识别“官话/套话”，用来把情报写成人话、并给稿件做降权。

用法:
  from jargon import jargon_hits, is_jargon_heavy
  jargon_hits("高质量发展取得新成效")  # -> 2
"""

from __future__ import annotations

JARGON = (
    "高质量发展", "深入实施", "动能向新", "结构向优", "统筹推进", "持续发力", "多措并举",
    "纵深推进", "扎实推进", "全面贯彻", "贯彻落实", "提质增效", "激发活力", "释放潜能",
    "激发潜能", "稳中求进", "新质生产力", "供给侧", "需求侧", "供需两端", "协同发力",
    "形成合力", "高质量发展", "赋能", "抓手", "闭环", "着力", "切实把", "进一步提升",
    "持续优化", "不断提升", "营造良好", "统筹协调", "系统推进", "落地见效", "走深走实",
    "坚定不移", "奋力", "谱写", "新篇章", "新格局", "开创新局面",
)

STRONG = (
    "高质量发展", "深入实施", "动能向新", "结构向优", "统筹推进", "持续发力", "多措并举",
    "纵深推进", "扎实推进", "全面贯彻", "贯彻落实", "提质增效", "激发活力", "释放潜能",
    "新质生产力", "落地见效", "走深走实", "形成合力", "协同发力",
)


def jargon_hits(text: str) -> int:
    t = text or ""
    return sum(1 for w in JARGON if w in t)


def strong_jargon_hits(text: str) -> int:
    t = text or ""
    return sum(1 for w in STRONG if w in t)


def is_jargon_heavy(text: str, threshold: int = 3) -> bool:
    return strong_jargon_hits(text) >= threshold


if __name__ == "__main__":
    import sys

    for s in sys.argv[1:]:
        print(f"{jargon_hits(s)}  {s}")

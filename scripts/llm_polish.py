"""用大模型把“抽取式初稿”改写成能直接发的人话。

读 config/brief.draft.json（make_brief.py 生成），调一个 OpenAI 兼容接口，
产出 config/brief.json。没配 key 就退出并提示。

环境变量（写在 .env 里也行，脚本会读）：
  LLM_BASE_URL  https://api.deepseek.com/v1   （OpenAI 兼容的 /chat/completions）
  LLM_API_KEY   sk-...
  LLM_MODEL     deepseek-chat

用法：
  python scripts/llm_polish.py                    # 初稿 -> 正式 brief
  python scripts/llm_polish.py --dry-run          # 只打印 prompt，不调接口
  python scripts/llm_polish.py --out config/brief.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SYSTEM = """你是中文日报的编辑，读者是普通人，不是官员。你的任务是把当天素材写成“人能读明白的分析”。

硬性规则：
1. 只用具体的东西说话：动作、数字、公司/人名、时间。不写官话套话。
2. 以下词一律不许出现在成稿里：高质量发展、深入实施、动能向新、结构向优、统筹推进、持续发力、多措并举、纵深推进、扎实推进、全面贯彻、贯彻落实、提质增效、激发活力、释放潜能、新质生产力、赋能、抓手、闭环、落地见效、走深走实、形成合力。
3. 素材里如果只有官话（比如“消费潜能不断释放”），你要把它翻译成人话：哪个行业、哪个品类、哪群人、变化了多少；找不到具体信息，就写“原文只有宏观表述，没有给出具体数据”，不要替它编。
4. 每条新闻的输出结构：
   - title：陈述句，不超过 26 字，不要问号，不要“为什么/如何”
   - lede：发生了什么，40~70 字，带最关键的数字
   - points：固定 3 条：① 关键事实（数字/动作）② 为什么重要或意味着什么 ③ 接下来看什么（时间/节点/悬念）
   - takeaway：3 条「对普通人有什么用」，要能执行（看什么指标、做什么判断）
   - questions：3 条「值得琢磨的问题」，要有反常识的假设或悬念，不要复述事实
5. 数字必须来自素材，不能编。
严格输出 JSON（不要 markdown 代码块）：
{"items":[{"event_id":"e1","title":"...","lede":"...","points":["...","...","..."],
           "takeaway":["...","...","..."],"questions":["...","...","..."]}, ...],
 "followup":{"event_id":"eX","title":"...","lede":"...","points":["..."],"takeaway":["..."],"questions":["..."]}}"""


def load_env_dict() -> dict:
    cfg = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_prompt(draft: dict) -> str:
    lines = []
    for it in draft.get("items", []):
        lines.append(f"[{it.get('event_id')}] {it.get('title')}")
        lines.append(f"  导语候选：{it.get('lede', '')[:120]}")
        for p in it.get("points", []):
            lines.append(f"  材料：{p[1][:120]}")
        for s in (it.get("web", {}).get("sources", []))[:2]:
            lines.append(f"  来源：{s.get('label', '')[:50]}")
    fu = draft.get("followup")
    if fu:
        lines.append(f"[后续 {fu.get('event_id', '')}] {fu.get('title')}")
        for p in fu.get("points", []):
            lines.append(f"  材料：{p[1][:120]}")
    lines.append("")
    lines.append("请输出 JSON。")
    return "\n".join(lines)


class ModelRefusal(RuntimeError):
    """模型拒答/内容过滤（不是限流，也不是超时）。"""


def extract_content(data: dict) -> str:
    """从 chat/completions 响应里取正文；空内容视为拒答。"""
    choice = (data.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content") or "").strip()
    finish = choice.get("finish_reason") or data.get("stop_reason") or ""
    if not content:
        raise ModelRefusal(f"模型没有返回内容（finish_reason={finish or 'empty'}）")
    return content


def call_llm(cfg: dict, prompt: str, timeout: int = 90, extra: str = "") -> str:
    url = cfg.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": cfg.get("LLM_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt + ("\n\n" + extra if extra else "")},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['LLM_API_KEY']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return extract_content(data)


def parse_json_block(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def merge(draft: dict, llm: dict) -> dict:
    by_id = {it.get("event_id"): it for it in draft.get("items", [])}
    out_items = []
    for item in llm.get("items", []):
        base = by_id.get(item.get("event_id"))
        if not base:
            continue
        title = str(item.get("title", "")).strip().rstrip("？?")
        points = [str(p).strip() for p in item.get("points", []) if str(p).strip()][:3]
        if not title or len(points) < 3:
            continue
        base["title"] = title[:38]
        base["lede"] = str(item.get("lede", "")).strip()[:150]
        base["points"] = [[f"{i} · ", p] for i, p in enumerate(points, 1)]
        take = [str(x).strip() for x in item.get("takeaway", []) if str(x).strip()][:4]
        questions = [str(x).strip() for x in item.get("questions", []) if str(x).strip()][:4]
        blocks = [b for b in base.get("web", {}).get("blocks", []) if b.get("type") not in ("takeaway", "question")]
        if take:
            blocks.append({"title": "普通人能学到什么 / 怎么用", "type": "takeaway", "lines": take})
        if questions:
            blocks.append({"title": "值得琢磨的几个问题", "type": "question", "lines": questions})
        if blocks:
            base.setdefault("web", {})["blocks"] = blocks
        out_items.append(base)
    if len(out_items) < 3:
        raise ValueError(f"模型只返回了 {len(out_items)} 条可用新闻，放弃覆盖")
    draft["items"] = out_items

    fu = draft.get("followup")
    lf = llm.get("followup")
    if fu and lf:
        title = str(lf.get("title", "")).strip().rstrip("？?")
        points = [str(p).strip() for p in lf.get("points", []) if str(p).strip()][:3]
        if title and len(points) == 3:
            fu["title"] = title[:38]
            fu["lede"] = str(lf.get("lede", "")).strip()[:150]
            fu["points"] = [[f"{i} · ", p] for i, p in enumerate(points, 1)]
            take = [str(x).strip() for x in lf.get("takeaway", []) if str(x).strip()][:4]
            if take:
                blocks = [b for b in fu.get("web", {}).get("blocks", []) if b.get("type") != "takeaway"]
                blocks.append({"title": "普通人能学到什么 / 怎么用", "type": "takeaway", "lines": take})
                fu.setdefault("web", {})["blocks"] = blocks
    draft["draft"] = False
    draft["doc_title"] = out_items[0]["title"]
    return draft


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", default=str(ROOT / "config" / "brief.draft.json"))
    parser.add_argument("--out", default=str(ROOT / "config" / "brief.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not Path(args.inp).exists():
        print(f"找不到初稿 {args.inp}：先跑 python scripts/make_brief.py")
        return 1
    draft = load_json(Path(args.inp))
    prompt = build_prompt(draft)
    cfg = load_env_dict()

    if args.dry_run:
        print(prompt)
        return 0
    if not cfg.get("LLM_API_KEY"):
        print("没有配置 LLM_API_KEY（.env 或环境变量），无法改写。")
        print("把 key 给我，或在 .env 里加：LLM_API_KEY=... / LLM_BASE_URL=... / LLM_MODEL=...")
        return 2

    try:
        raw = call_llm(cfg, prompt)
    except ModelRefusal as exc:
        print(f"  ⚠ {exc}：可能触发了内容策略（不是限流）。")
        print("  用中性措辞重试一次……")
        soft = ("如果内容敏感，请只做中性客观陈述：不评价、不渲染细节、不复述未经证实的指控，"
                "保留数字与已公开事实。")
        try:
            raw = call_llm(cfg, prompt, extra=soft)
        except ModelRefusal as exc2:
            print(f"✗ 仍被拒答：{exc2}")
            print("  降级：保留抽取式初稿（质量糙一点），不中断流水线。")
            return 3
    llm = parse_json_block(raw)
    brief = merge(draft, llm)

    from jargon import strong_jargon_hits

    def all_text(b: dict) -> str:
        parts = []
        for it in b.get("items", []):
            parts.append(it.get("title", "") + it.get("lede", ""))
            parts.extend(p[1] for p in it.get("points", []))
        fu = b.get("followup") or {}
        parts.append(fu.get("title", "") + fu.get("lede", ""))
        return "".join(parts)

    if strong_jargon_hits(all_text(brief)) >= 4:
        print("  官话太多，要求模型重写一遍……")
        try:
            raw2 = call_llm(cfg, prompt, extra=(
                "上一版官话太多。请把每条都改写成具体的人话："
                "不许出现“高质量发展/深入实施/动能向新/结构向优/统筹推进/释放潜能/赋能/抓手”等词，"
                "把套话换成具体的行业、数字、人群、动作。数字仍必须来自素材。"))
            brief2 = merge(draft, parse_json_block(raw2))
            if strong_jargon_hits(all_text(brief2)) < strong_jargon_hits(all_text(brief)):
                brief = brief2
        except ModelRefusal as exc:
            print(f"  ⚠ 重写时被拒答（{exc}），保留当前版本")
        except ValueError as exc:
            print(f"  ⚠ 重写结果不可用（{exc}），保留当前版本")
    Path(args.out).write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已改写并写入 {args.out}")
    for it in brief["items"]:
        print(f"  - {it['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

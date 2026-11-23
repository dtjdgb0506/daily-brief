"""回归测试（标准库 unittest，不需要 pytest）。

跑法：
  python -m unittest discover -s tests -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_card  # noqa: E402
import collect  # noqa: E402

RSS_SAMPLE = """<rss version="2.0"><channel>
<item><title>测试标题一</title><link>https://example.com/a</link>
      <pubDate>Sat, 21 Nov 2026 10:00:00 +0800</pubDate></item>
<item><title>测试标题二</title><link>https://example.com/b</link></item>
</channel></rss>"""


class TestFeedParse(unittest.TestCase):
    def test_parse_rss(self):
        items = collect.parse_feed(RSS_SAMPLE)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "测试标题一")
        self.assertTrue(items[0]["published"].startswith("2026-11-21"))

    def test_bad_xml(self):
        self.assertEqual(collect.parse_feed("<html>not xml"), [])


class TestValidate(unittest.TestCase):
    def _item(self, **kw):
        item = {
            "title": "英伟达因出轨被辞退",
            "lede": "一句话",
            "points": [["1 ·", "第一点"], ["2 ·", "第二点"], ["3 ·", "第三点"]],
            "web": {"sources": [{"label": "x", "url": "https://example.com/x"}]},
        }
        item.update(kw)
        return item

    def test_question_title(self):
        errs, _ = build_card.validate({"items": [self._item(title="黄仁勋为什么下台？")]}, None)
        self.assertTrue(any("问句" in e for e in errs), errs)

    def test_duplicate_url(self):
        a = self._item()
        b = self._item(title="另一条标题")
        errs, _ = build_card.validate({"items": [a, b]}, None)
        self.assertTrue(any("重复" in e for e in errs), errs)

    def test_number_needs_source(self):
        item = self._item(web={"sources": []}, points=[["1 ·", "跌了 44%"]])
        errs, _ = build_card.validate({"items": [item]}, None)
        self.assertTrue(any("没有出处" in e for e in errs), errs)

    def test_valid_brief_passes(self):
        items = [
            self._item(),
            self._item(title="Anthropic 上市募资 813 亿美元",
                       web={"sources": [{"label": "y", "url": "https://example.com/y"}]}),
            self._item(title="也门遭空袭致 41 人死亡",
                       web={"sources": [{"label": "z", "url": "https://example.com/z"}]}),
        ]
        errs, _ = build_card.validate({"items": items, "followup": None}, None)
        self.assertEqual(errs, [])


class TestClusterFixture(unittest.TestCase):
    def test_fixture_5_plus_1(self):
        out = Path(tempfile.mkdtemp())
        cmd = [
            sys.executable, str(ROOT / "scripts" / "cluster_events.py"),
            "--fixture", str(ROOT / "tests" / "fixture_articles.json"),
            "--history-dir", str(ROOT / "tests" / "fixture_archive"),
            "--date", "2026-11-22", "--no-archive", "--out", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads((out / "events.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(data["event_total"], 6)
        self.assertEqual(len(data["selected"]), 5)
        self.assertIsNotNone(data["followup"], "应该识别出 1 条历史后续")
        follow = next(e for e in data["events"] if e["is_followup"])
        self.assertIn("美股", follow["followup_of"]["title"])


if __name__ == "__main__":
    unittest.main()

class TestMakeBrief(unittest.TestCase):
    def test_draft_passes_validation(self):
        import make_brief  # noqa: F401
        out = Path(tempfile.mkdtemp()) / "brief.json"
        cmd = [
            sys.executable, str(ROOT / "scripts" / "make_brief.py"),
            "--events", str(ROOT / "output" / "events.json"),
            "--raw", str(ROOT / "output" / "raw"),
            "--out", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not out.exists():
            self.skipTest("还没有 events.json，跳过自动初稿测试")
        brief = json.loads(out.read_text(encoding="utf-8"))
        errs, _ = build_card.validate(brief, None)
        self.assertEqual(errs, [])

class TestLlmPolish(unittest.TestCase):
    def test_parse_json_block(self):
        import llm_polish
        raw = "```json\n{\"a\": 1}\n```"
        self.assertEqual(llm_polish.parse_json_block(raw), {"a": 1})

    def test_merge_rejects_bad_items(self):
        import llm_polish
        draft = {"items": [
            {"event_id": f"e{i}", "title": f"old{i}", "lede": "", "points": [], "web": {}}
            for i in range(1, 5)
        ], "followup": None}
        llm = {"items": [
            {"event_id": "e1", "title": "new1?", "lede": "lede", "points": ["a", "b", "c"]},
            {"event_id": "e2", "title": "new2", "lede": "lede", "points": ["a", "b", "c"]},
            {"event_id": "e3", "title": "new3", "lede": "lede", "points": ["a", "b", "c"]},
            {"event_id": "e4", "title": "", "lede": "lede", "points": ["a"]},
        ]}
        out = llm_polish.merge(draft, llm)
        self.assertEqual(len(out["items"]), 3)
        self.assertEqual(out["items"][0]["title"], "new1")
        self.assertFalse(out["draft"])


class TestQualityFilter(unittest.TestCase):
    """选题质量回归：展会/活动稿不能进日报，硬新闻要保留。"""

    def test_soft_news_detection(self):
        import cluster_events as ce
        self.assertTrue(ce.is_soft_news("2026年广西林木展吸引国际头部企业及境外采购商"))
        self.assertTrue(ce.is_soft_news("CHINA ROCK 科技创新工业展览会在成都启幕"))
        self.assertTrue(ce.is_soft_news("某市举行招商签约仪式"))
        self.assertFalse(ce.is_soft_news("美国参议院通过新一轮芯片出口管制法案"))
        self.assertFalse(ce.is_soft_news("英伟达发布新一代 GPU，算力提升 40%"))

    def test_quality_fixture_excludes_exhibitions(self):
        out = Path(tempfile.mkdtemp())
        cmd = [
            sys.executable, str(ROOT / "scripts" / "cluster_events.py"),
            "--fixture", str(ROOT / "tests" / "fixture_quality.json"),
            "--date", "2026-11-22", "--no-archive", "--out", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads((out / "events.json").read_text(encoding="utf-8-sig"))
        titles = [e["representative_title"] for e in data["events"]]
        self.assertTrue(titles, "真新闻不应该被过滤光")
        for title in titles:
            self.assertNotIn("展", title, f"展会稿不应进事件列表：{title}")
        self.assertTrue(data["selected"], "至少应选出 1 条")


class TestCollectQuota(unittest.TestCase):
    """抓正文配额：每个源最多 per_feed 篇，再按时间取最新的。"""

    def test_per_feed_cap_and_recency(self):
        cands = []
        for feed in ("A", "B"):
            for i in range(10):
                cands.append({
                    "feed": feed,
                    "url": f"https://example.com/{feed}/{i}",
                    "title": f"{feed}{i}",
                    "published": f"2026-11-21T{i:02d}:00:00+08:00",
                })
        todo = collect.pick_todo(cands, limit=6, per_feed=3)
        self.assertEqual(len(todo), 6)
        self.assertEqual(sum(1 for t in todo if t["feed"] == "A"), 3)
        self.assertEqual(sum(1 for t in todo if t["feed"] == "B"), 3)
        self.assertEqual({t["title"] for t in todo if t["feed"] == "A"}, {"A9", "A8", "A7"})

    def test_limit_never_exceeded(self):
        cands = [{"feed": "A", "url": f"https://example.com/{i}", "title": str(i),
                  "published": "2026-11-21T10:00:00+08:00"} for i in range(5)]
        self.assertEqual(len(collect.pick_todo(cands, limit=3, per_feed=4)), 3)


class TestSendFromState(unittest.TestCase):
    """准点发送任务：没有准备好的卡片时必须报错（让工作流回退到现跑）。"""

    def test_missing_card_returns_error(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "send_from_state.py"),
             "--date", "1999-01-01", "--dry-run"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("没有准备好的卡片", proc.stdout + proc.stderr)


class TestLlmRefusal(unittest.TestCase):
    """拒答（refusal / content_filter）要能被识别，并降级而不是崩掉。"""

    def test_normal_content(self):
        import llm_polish
        data = {"choices": [{"message": {"content": '{"ok": 1}'}, "finish_reason": "stop"}]}
        self.assertEqual(llm_polish.extract_content(data), '{"ok": 1}')

    def test_empty_content_raises(self):
        import llm_polish
        for data in (
            {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]},
            {"choices": [{"message": {"content": "   "}, "finish_reason": "refusal"}]},
            {"choices": []},
        ):
            with self.assertRaises(llm_polish.ModelRefusal):
                llm_polish.extract_content(data)


if __name__ == "__main__":
    unittest.main()

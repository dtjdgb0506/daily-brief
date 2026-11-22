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

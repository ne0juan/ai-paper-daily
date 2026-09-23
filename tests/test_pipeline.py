"""Integration: a full run on fixtures, twice, then build + verify the static site."""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from radar.llm import LLM
from radar.pipeline import run, slot_for
from tests.helpers import NOW, fake_http, fake_llm_transport

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_site import build  # noqa: E402
from verify_dist import verify  # noqa: E402


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data, self.cache, self.dist = self.tmp / "data", self.tmp / "cache", self.tmp / "dist"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, now=NOW, llm=None, http=None):
        return run(now=now, data_dir=self.data, cache_dir=self.cache, http=http or fake_http(),
                   llm=llm or LLM(transport=fake_llm_transport), snapshots=False)

    def test_slots(self):
        self.assertEqual(slot_for(NOW), "noon")                       # 11:00 BJ
        self.assertEqual(slot_for(NOW - timedelta(hours=3)), "morning")
        self.assertEqual(slot_for(NOW + timedelta(hours=8)), "evening")

    def test_full_run_then_no_repeats_then_build(self):
        info = self._run()
        self.assertGreater(info["selected"], 3)
        self.assertTrue(info["llm"])
        day = json.loads((self.data / "days" / "2026-09-23.json").read_text())
        ids = [i["id"] for i in day["items"]]
        self.assertIn("arxiv-2609.01234", ids)
        self.assertFalse(any("sourdough" in i["title"].lower() for i in day["items"]), "LLM marked irrelevant")
        top = next(i for i in day["items"] if i["id"] == "arxiv-2609.01234")
        self.assertEqual(top["mirror_pdf"], "pdf/arxiv-2609.01234.pdf")
        self.assertTrue((self.cache / "arxiv-2609.01234.pdf").exists())
        self.assertTrue(any(l["label"] == "alphaXiv 讨论" for l in top["links"]))
        self.assertEqual(top["slot"], "noon")

        # a later run the same day must not re-select anything already published
        info2 = self._run(now=NOW + timedelta(hours=8))
        day2 = json.loads((self.data / "days" / "2026-09-23.json").read_text())
        self.assertEqual(len(day2["items"]), len(ids) + info2["selected"])
        self.assertEqual(len({i["id"] for i in day2["items"]}), len(day2["items"]))

        index = json.loads((self.data / "index.json").read_text())
        self.assertEqual(index["days"][0]["date"], "2026-09-23")
        self.assertEqual(index["total"], len(day2["items"]))

        stats = build(self.data, self.cache, self.dist, "https://radar.example.com", retain_days=3650)
        self.assertGreater(stats["pdfs"], 0)
        self.assertEqual(verify(self.dist), [])
        self.assertIn("<rss", (self.dist / "feed.xml").read_text())

    def test_without_llm_uses_rules_and_heuristics(self):
        llm = LLM(api_key="")
        llm.api_key = ""
        info = self._run(llm=llm)
        self.assertFalse(info["llm"])
        day = json.loads((self.data / "days" / "2026-09-23.json").read_text())
        self.assertTrue(all(i["tags"] for i in day["items"]))
        self.assertTrue(all(i["summary_zh"] for i in day["items"] if i["kind"] == "paper"))

    def test_all_sources_down(self):
        from radar.http import FakeHttp
        info = self._run(http=FakeHttp({}))
        self.assertEqual(info["candidates"], 0)
        self.assertFalse((self.data / "days").exists())

    def test_verify_catches_broken_mirror(self):
        self._run()
        build(self.data, self.cache, self.dist, retain_days=3650)
        pdf = next((self.dist / "pdf").glob("*.pdf"))
        pdf.write_bytes(b"<html>oops</html>")
        errs = verify(self.dist)
        self.assertTrue(any("broken mirror" in e for e in errs))

    def test_expired_pdfs_are_unlinked(self):
        self._run()
        build(self.data, self.cache, self.dist, retain_days=0)   # everything "expired"... except today
        day = json.loads((self.dist / "data/days/2026-09-23.json").read_text())
        self.assertEqual(verify(self.dist), [])
        self.assertIsInstance(day["items"], list)


if __name__ == "__main__":
    unittest.main()

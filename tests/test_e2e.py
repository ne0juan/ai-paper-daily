"""Browser E2E: build the site from a fixture run, serve it, drive it with Chromium.
Screenshots land in $E2E_SHOTS (default: .e2e/) for visual review."""
import functools
import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from radar.llm import LLM
from radar.pipeline import run
from tests.helpers import NOW, fake_http, fake_llm_transport

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_site import build  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

SHOTS = Path(os.environ.get("E2E_SHOTS", ROOT / ".e2e"))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        data, cache = cls.tmp / "data", cls.tmp / "cache"
        llm = LLM(transport=fake_llm_transport)
        run(now=NOW - timedelta(days=1), data_dir=data, cache_dir=cache, http=fake_http(), llm=llm, snapshots=False)
        # second day: morning + evening issues
        import radar.pipeline as p
        state = p.load_json(data / "state.json", {})
        state["selected"] = {}
        p.dump_json(data / "state.json", state)
        run(now=NOW - timedelta(hours=3), data_dir=data, cache_dir=cache, http=fake_http(), llm=llm, snapshots=False)
        cls.dist = cls.tmp / "dist"
        build(data, cache, cls.dist, retain_days=3650)
        handler = functools.partial(Quiet, directory=str(cls.dist))
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        SHOTS.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def page(self, **kw):
        ctx = self.browser.new_context(**kw)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(self.url)
        page.wait_for_selector("body[data-ready='1']", timeout=10000)
        return ctx, page, errors

    def test_renders_must_read_then_newest_issue_first(self):
        ctx, page, errors = self.page(viewport={"width": 1280, "height": 900})
        cards = page.locator(".card")
        self.assertGreater(cards.count(), 3)
        self.assertEqual(page.locator("#blips .blip").count(), cards.count())
        self.assertEqual(page.locator(".must .card").count(), 3)
        heads = page.locator(".section:not(.must) .sec-head h2").all_inner_texts()
        order = [h for h in ["晚间刊", "午间刊", "晨间刊"] if h in heads]
        self.assertEqual(heads, order, "issues must be newest first")
        # must-read items are not repeated in the slot sections
        ids = page.eval_on_selector_all(".card", "els => els.map(e => e.dataset.id)")
        self.assertEqual(len(ids), len(set(ids)))
        # must-read are the highest scores of the day
        must = page.eval_on_selector_all(".must .score", "els => els.map(e => +e.textContent)")
        rest = page.eval_on_selector_all(".section:not(.must) .score", "els => els.map(e => +e.textContent)")
        self.assertGreaterEqual(min(must), max(rest))
        # the primary button serves our mirrored PDF
        href = page.locator(".primary[href^='pdf/']").first.get_attribute("href")
        resp = page.request.get(self.url + href)
        self.assertEqual((resp.status, resp.body()[:5]), (200, b"%PDF-"))
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "desktop.png"), full_page=True)
        self.assertEqual(errors, [])
        ctx.close()

    def test_new_since_last_visit(self):
        ctx = self.browser.new_context()
        ctx.add_init_script("localStorage.setItem('pr-last-visit', String(Date.parse('2026-09-22T12:00:00Z')))")
        page = ctx.new_page()
        page.goto(self.url)
        page.wait_for_selector("body[data-ready='1']")
        n_new = page.locator(".card .new").count()
        self.assertGreater(n_new, 0)
        self.assertIn(f"新增 {n_new} 篇", page.inner_text("#since"))
        page.goto(self.url + "#/2026-09-22")
        page.wait_for_function("document.querySelector('#issue-line').textContent.includes('22日')")
        self.assertEqual(page.locator(".card .new").count(), 0, "yesterday's items predate the last visit")
        ctx.close()

    def test_days_radar_theme_more(self):
        ctx, page, errors = self.page()
        days = page.locator(".day")
        self.assertEqual(days.count(), 2)
        self.assertIn("今天", days.first.inner_text())
        days.nth(1).click()
        page.wait_for_function("location.hash === '#/2026-09-22'")
        page.wait_for_function("document.querySelector('#issue-line').textContent.includes('22日')")
        page.locator("#blips .blip").first.click(force=True)
        page.wait_for_selector(".card.flash")
        page.locator(".more summary").first.click()
        self.assertTrue(page.locator(".more[open] .links a").first.is_visible())
        page.locator("#theme").click()
        self.assertIn(page.evaluate("document.documentElement.dataset.theme"), ("dark", "light"))
        page.screenshot(path=str(SHOTS / "theme-toggled.png"))
        self.assertEqual(errors, [])
        ctx.close()

    def test_mobile_layout_no_horizontal_scroll(self):
        ctx, page, errors = self.page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - window.innerWidth"), 0)
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "mobile.png"), full_page=True)
        self.assertEqual(errors, [])
        ctx.close()

    def test_deep_link_to_item(self):
        ctx, page, _ = self.page()
        page.goto(self.url + "#/item/arxiv-2609.01234")
        page.wait_for_selector(".card.flash[data-id='arxiv-2609.01234']", timeout=8000)
        ctx.close()

    def test_empty_site(self):
        empty = self.tmp / "empty"
        build(self.tmp / "nodata", self.tmp / "nocache", empty)
        # served from a second server rooted at the empty build
        handler = functools.partial(Quiet, directory=str(empty))
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        ctx = self.browser.new_context()
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
        page.wait_for_selector(".empty")
        self.assertIn("预热", page.inner_text("#feed"))
        self.assertEqual(errs, [])
        ctx.close()
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()

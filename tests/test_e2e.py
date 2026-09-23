"""Browser E2E on a realistic demo day (tests/fixtures/demo_day.json) plus a fixture pipeline
day, then the design-QA standard. Screenshots land in $E2E_SHOTS (default: .e2e/)."""
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

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


def serve(directory: Path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.dist = cls.tmp / "dist"
        subprocess.run([sys.executable, str(ROOT / "scripts/demo_dist.py"), str(cls.dist)], check=True, capture_output=True)
        # add a second (older) day so the date strip and archive can be tested
        prev = json.loads((cls.dist / "data/days/2026-09-23.json").read_text("utf-8"))
        for it in prev["items"]:
            it["id"] += "-old"
            it["date"] = "2026-09-22"
            it["selected_at"] = it["selected_at"].replace("09-23", "09-22")
            it["mirror_pdf"] = ""
        (cls.dist / "data/days/2026-09-22.json").write_text(json.dumps(prev, ensure_ascii=False), "utf-8")
        idx = json.loads((cls.dist / "data/index.json").read_text("utf-8"))
        idx["days"].append({"date": "2026-09-22", "count": len(prev["items"])})
        (cls.dist / "data/index.json").write_text(json.dumps(idx, ensure_ascii=False), "utf-8")
        cls.httpd, cls.url = serve(cls.dist)
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

    def test_reading_order(self):
        ctx, page, errors = self.page(viewport={"width": 1280, "height": 900})
        heads = page.locator(".sec-head h2").all_inner_texts()
        self.assertEqual(heads[0], "今日必读")
        self.assertEqual(heads[-1], "论文", "papers come last")
        slots = [h for h in heads if h.endswith("刊")]
        self.assertEqual(slots, [h for h in ["晚间刊", "午间刊", "晨间刊"] if h in slots], "newest issue first")
        ids = page.eval_on_selector_all(".card", "els => els.map(e => e.dataset.id)")
        self.assertEqual(len(ids), len(set(ids)), "no item shown twice")
        self.assertEqual(page.locator(".must .card").count(), 3)
        self.assertEqual(page.locator(".must .cat", has_text="论文").count(), 0, "papers never in 今日必读")
        # opinion cards lead with the person
        byline = page.locator(".card:has(.cat.op) .byline").first.inner_text()
        self.assertIn("｜", byline)
        # paper card: plain-language explanation + one-click Chinese full text
        paper = page.locator(".papers .card").first
        self.assertEqual(paper.locator(".explain").count(), 1)
        self.assertTrue(paper.locator(".secondary", has_text="中文全文").is_visible())
        # reading button serves our archived copy
        href = page.locator(".primary[href^='pdf/']").first.get_attribute("href")
        resp = page.request.get(self.url + href)
        self.assertEqual((resp.status, resp.body()[:5]), (200, b"%PDF-"))
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "desktop.png"), full_page=True)
        self.assertEqual(errors, [])
        ctx.close()

    def test_new_since_last_visit(self):
        ctx = self.browser.new_context()
        ctx.add_init_script("localStorage.setItem('pr-last-visit', String(Date.parse('2026-09-23T03:00:00Z')))")
        page = ctx.new_page()
        page.goto(self.url)
        page.wait_for_selector("body[data-ready='1']")
        n_new = page.locator(".card .new").count()
        self.assertGreater(n_new, 0)
        self.assertLess(n_new, page.locator(".card").count(), "morning items predate the visit")
        self.assertIn(f"新增 {n_new} 条", page.inner_text("#since"))
        ctx.close()

    def test_days_radar_theme_more(self):
        ctx, page, errors = self.page()
        days = page.locator(".day")
        self.assertEqual(days.count(), 2)
        self.assertIn("今天", days.first.inner_text())
        days.nth(1).click()
        page.wait_for_function("location.hash === '#/2026-09-22'")
        page.wait_for_function("document.querySelector('#issue-line').textContent.includes('22 日')")
        self.assertIn("往期", page.inner_text("#since"))
        page.locator("#blips .blip").first.click(force=True)
        page.wait_for_selector(".card.flash")
        page.locator(".more summary").first.click()
        self.assertTrue(page.locator(".more[open] .links a").first.is_visible())
        page.locator("#theme").click()
        self.assertIn(page.evaluate("document.documentElement.dataset.theme"), ("dark", "light"))
        self.assertEqual(errors, [])
        ctx.close()

    def test_deep_link_to_item(self):
        ctx, page, _ = self.page()
        page.goto(self.url + "#/item/web-demo5")
        page.wait_for_selector(".card.flash[data-id='web-demo5']", timeout=8000)
        ctx.close()

    def test_brief_renders_when_present(self):
        d = self.tmp / "brief"
        shutil.copytree(self.dist, d)
        day = json.loads((d / "data/days/2026-09-23.json").read_text("utf-8"))
        day["brief"] = [{"trend": "推理成本继续下探，价格战升级", "why": "DeepSeek 与 OpenAI 同日降价。", "refs": ["web-demo2", "web-demo4"]}]
        (d / "data/days/2026-09-23.json").write_text(json.dumps(day, ensure_ascii=False), "utf-8")
        srv, url = serve(d)
        ctx = self.browser.new_context()
        page = ctx.new_page()
        page.goto(url)
        page.wait_for_selector("body[data-ready='1']")
        self.assertIn("价格战", page.inner_text(".brief"))
        self.assertEqual(page.locator(".brief .refs a").count(), 2)
        page.locator(".brief .refs a").first.click()
        page.wait_for_selector(".card.flash[data-id='web-demo2']", timeout=8000)
        ctx.close()
        srv.shutdown()

    def test_design_standard(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts/design_qa.py"), str(self.dist), str(SHOTS)],
                           capture_output=True, text=True)
        report = json.loads((SHOTS / "design_qa.json").read_text("utf-8"))
        self.assertEqual(report["failed"], [], r.stdout[-2000:])

    def test_empty_site(self):
        empty = self.tmp / "empty"
        build(self.tmp / "nodata", self.tmp / "nocache", empty)
        srv, url = serve(empty)
        ctx = self.browser.new_context()
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url)
        page.wait_for_selector(".empty")
        self.assertIn("预热", page.inner_text("#feed"))
        self.assertEqual(errs, [])
        ctx.close()
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()

"""Mirror originals so they're reachable from any network:
- papers: download the original PDF (arXiv or direct .pdf link)
- articles: render a print-quality PDF snapshot of the page with headless Chromium
Files live in ``cache_dir`` (persisted between runs via actions/cache) and are copied
into the site at build time."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .http import HttpError
from .models import Item

log = logging.getLogger(__name__)
MAX_BYTES = 24 * 1024 * 1024  # Cloudflare Pages per-file limit is 25 MiB


def safe_name(item_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", item_id) + ".pdf"


def is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(5) == b"%PDF-" and path.stat().st_size > 1024
    except OSError:
        return False


def mirror_pdf(http, it: Item, cache_dir: Path) -> bool:
    dest = cache_dir / safe_name(it.id)
    if is_pdf(dest):
        return True
    if not it.pdf_url:
        return False
    try:
        http.download(it.pdf_url, dest, MAX_BYTES)
    except (HttpError, OSError) as e:
        log.warning("pdf %s: %s", it.id, e)
        return False
    if not is_pdf(dest):
        log.warning("pdf %s: not a PDF", it.id)
        dest.unlink(missing_ok=True)
        return False
    return True


BANNER_JS = """
(meta) => {
  const d = document.createElement('div');
  d.style.cssText = 'font:12px/1.5 -apple-system,Segoe UI,sans-serif;padding:8px 12px;margin:0 0 12px;' +
    'border:1px solid #ccc;border-radius:6px;background:#faf7f0;color:#333;word-break:break-all';
  d.textContent = '原文快照 · ' + meta.url + ' · 抓取于 ' + meta.at + '（Paper Radar 镜像，仅供内部学习参考）';
  document.body.prepend(d);
  document.querySelectorAll('[class*="cookie" i],[id*="cookie" i],[class*="consent" i]').forEach(e => e.remove());
}
"""
BLOCKED_MARKERS = ("just a moment", "attention required", "access denied", "verify you are human",
                   "enable javascript and cookies")


class Snapshotter:
    """Lazily starts Chromium; renders article pages to PDF."""

    def __init__(self):
        self._pw = self._browser = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _ensure(self) -> bool:
        if self._browser:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("snapshot disabled (no chromium): %s", e)
            return False

    def snapshot(self, it: Item, cache_dir: Path) -> bool:
        dest = cache_dir / safe_name(it.id)
        if is_pdf(dest):
            return True
        if not self._ensure():
            return False
        ctx = self._browser.new_context(locale="en-US", viewport={"width": 1200, "height": 900},
                                        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"))
        page = ctx.new_page()
        try:
            page.goto(it.url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            # trigger lazy images
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            text = (page.inner_text("body") or "")[:5000].lower()
            title = (page.title() or "").lower()
            if len(text) < 400 or any(m in title or m in text[:400] for m in BLOCKED_MARKERS):
                log.warning("snapshot %s: blocked or empty page", it.url)
                return False
            page.evaluate(BANNER_JS, {"url": it.url,
                                      "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")})
            dest.parent.mkdir(parents=True, exist_ok=True)
            page.emulate_media(media="screen")
            page.pdf(path=str(dest), format="A4", print_background=True,
                     margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
            if not is_pdf(dest) or dest.stat().st_size > MAX_BYTES:
                dest.unlink(missing_ok=True)
                return False
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("snapshot %s: %s", it.url, e)
            return False
        finally:
            ctx.close()


def mirror_all(http, items: list[Item], cache_dir: Path, snapshots: bool = True) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with Snapshotter() as snap:
        for it in items:
            if it.pdf_url:
                ok, kind = mirror_pdf(http, it, cache_dir), "pdf"
            elif snapshots:
                ok, kind = snap.snapshot(it, cache_dir), "snapshot"
            else:
                ok, kind = False, ""
            if ok:
                it.mirror_pdf, it.mirror_kind = f"pdf/{safe_name(it.id)}", kind

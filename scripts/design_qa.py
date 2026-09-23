"""Design QA: measure the rendered site against a Chinese-reading typography standard.

Benchmarks (what the best Chinese reading sites do — 端传媒、少数派、FT中文网、澎湃,
plus W3C《中文排版需求》clreq):
  正文字号 ≥ 16px，行高 1.7–1.9；每行 28–42 个汉字（桌面）；
  中文不使用斜体；中英文之间留空（盘古之白）；
  正文对比度 ≥ 7:1，辅助信息 ≥ 4.5:1；一种强调色；
  字号阶梯克制（≤ 8 种）；手机端触控目标 ≥ 44px，无横向滚动；
  首屏必须看到第一条内容标题（报纸「头条在折线上方」原则）。

Usage:  python scripts/design_qa.py <url-or-dist-dir> [out_dir]
Exit code 1 if any HARD check fails. Writes out_dir/design_qa.json + screenshots.
"""
from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

MEASURE_JS = r"""
() => {
  const CJK = /[一-鿿]/;
  const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => { const f = (c) => { c /= 255; return c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length >= 3 && (c.length < 4 || c[3] > .5)) return c; } return [255, 255, 255]; };
  const contrast = (el) => { const a = lum(rgb(getComputedStyle(el).color)), b = lum(bgOf(el));
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05); };
  const q = (s) => [...document.querySelectorAll(s)].filter((e) => e.offsetParent !== null);
  const vis = (els) => els.filter((e) => e.getBoundingClientRect().width > 0);
  const summary = vis(q(".summary"));
  const s0 = summary[0], cs = s0 && getComputedStyle(s0);
  const fs = cs ? parseFloat(cs.fontSize) : 0;
  const lh = cs ? parseFloat(cs.lineHeight) / fs : 0;
  const cpl = s0 ? s0.getBoundingClientRect().width / fs : 0;
  // italics on CJK text
  const italicCJK = [...document.querySelectorAll("body *")].filter((e) => e.childNodes.length && [...e.childNodes]
    .some((n) => n.nodeType === 3 && CJK.test(n.textContent)) && getComputedStyle(e).fontStyle === "italic").length;
  // pangu spacing: CJK directly touching Latin letters/digits in headlines & summaries
  let pangu = 0; const samples = [];
  for (const e of q(".card h3, .summary, .why, .sec-head h2, #since")) {
    const t = e.textContent; const m = t.match(/[一-鿿][A-Za-z0-9]|[A-Za-z0-9][一-鿿]/g);
    if (m) { pangu += m.length; if (samples.length < 3) samples.push(m[0]); }
  }
  const minContrast = (sel) => { const els = vis(q(sel)); return els.length ? Math.min(...els.map(contrast)) : 99; };
  const fontSizes = new Set([...document.querySelectorAll("body *")].filter((e) => e.offsetParent !== null && e.textContent.trim())
    .map((e) => getComputedStyle(e).fontSize));
  const targets = vis(q(".primary, .secondary, .day, .more summary, #theme"));
  // duplicated labels inside a card's meta/byline (e.g. the same source name twice)
  let dupMeta = 0;
  for (const c of q(".card")) { const parts = [...c.querySelectorAll(".meta > span, .byline")].map((e) => e.textContent.trim());
    const words = parts.join(" ").split(/[\s｜·]+/).filter((w) => w.length > 2);
    if (new Set(words).size < words.length) dupMeta++; }
  const minTarget = targets.length ? Math.min(...targets.map((e) => e.getBoundingClientRect().height)) : 0;
  const firstH = q(".brief h2, .card h3")[0];   // the top story: trend brief or first headline
  return {
    body_font_px: fs, body_line_height: +lh.toFixed(2), chars_per_line: Math.round(cpl),
    italic_cjk_elements: italicCJK, pangu_violations: pangu, pangu_samples: samples,
    contrast_body: +minContrast(".summary").toFixed(2), contrast_title: +minContrast(".card h3").toFixed(2),
    contrast_meta: +minContrast(".meta, .orig, .sec-head span").toFixed(2),
    font_size_steps: fontSizes.size, min_tap_target_px: Math.round(minTarget),
    first_headline_top: firstH ? Math.round(firstH.getBoundingClientRect().top + scrollY) : 99999,
    viewport_h: innerHeight, overflow_x: document.documentElement.scrollWidth - innerWidth,
    cards: q(".card").length, duplicated_meta_cards: dupMeta,
  };
}
"""

HARD = {
    "desktop": [
        ("body_font_px", lambda v: v >= 16, "正文字号 ≥ 16px"),
        ("body_line_height", lambda v: 1.7 <= v <= 1.95, "正文行高 1.7–1.95"),
        ("chars_per_line", lambda v: 28 <= v <= 42, "每行 28–42 字"),
        ("italic_cjk_elements", lambda v: v == 0, "中文不用斜体"),
        ("pangu_violations", lambda v: v == 0, "中英文之间留空"),
        ("contrast_body", lambda v: v >= 7, "正文对比度 ≥ 7:1"),
        ("contrast_title", lambda v: v >= 7, "标题对比度 ≥ 7:1"),
        ("contrast_meta", lambda v: v >= 4.5, "辅助文字对比度 ≥ 4.5:1"),
        ("font_size_steps", lambda v: v <= 8, "字号阶梯 ≤ 8 种"),
        ("first_headline_top", lambda v: v <= 800, "首屏可见第一条标题"),
        ("overflow_x", lambda v: v <= 0, "无横向滚动"),
        ("console_errors", lambda v: v == 0, "无脚本错误"),
        ("duplicated_meta_cards", lambda v: v == 0, "卡片信息不重复"),
    ],
    "mobile": [
        ("body_font_px", lambda v: v >= 16, "正文字号 ≥ 16px"),
        ("chars_per_line", lambda v: 16 <= v <= 24, "每行 16–24 字"),
        ("min_tap_target_px", lambda v: v >= 44, "触控目标 ≥ 44px"),
        ("first_headline_top", lambda v: v <= 812 * 0.8, "首屏可见第一条标题"),
        ("overflow_x", lambda v: v <= 0, "无横向滚动"),
        ("contrast_meta", lambda v: v >= 4.5, "辅助文字对比度 ≥ 4.5:1"),
        ("console_errors", lambda v: v == 0, "无脚本错误"),
    ],
    "dark": [
        ("contrast_body", lambda v: v >= 7, "暗色正文对比度 ≥ 7:1"),
        ("contrast_meta", lambda v: v >= 4.5, "暗色辅助文字对比度 ≥ 4.5:1"),
    ],
}

VIEWS = {
    "desktop": dict(viewport={"width": 1280, "height": 800}),
    "mobile": dict(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True, device_scale_factor=2),
    "dark": dict(viewport={"width": 1280, "height": 800}, color_scheme="dark"),
}


def serve(dist: Path) -> str:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(dist))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/"


def run(target: str, out: Path) -> dict:
    url = serve(Path(target)) if Path(target).is_dir() else target
    out.mkdir(parents=True, exist_ok=True)
    report = {"url": url, "views": {}, "failed": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, opts in VIEWS.items():
            ctx = browser.new_context(**opts)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            page.goto(url)
            page.wait_for_selector("body[data-ready='1']", timeout=15000)
            page.wait_for_timeout(1200)
            m = page.evaluate(MEASURE_JS)
            m["console_errors"] = len(errors)
            checks = []
            for key, ok, label in HARD[name]:
                passed = bool(ok(m[key]))
                checks.append({"check": label, "value": m[key], "pass": passed})
                if not passed:
                    report["failed"].append(f"[{name}] {label}: {m[key]}")
            report["views"][name] = {"metrics": m, "checks": checks}
            page.screenshot(path=str(out / f"qa-{name}.png"), full_page=(name != "dark"))
            page.screenshot(path=str(out / f"qa-{name}-fold.png"))
            ctx.close()
        browser.close()
    total = sum(len(v["checks"]) for v in report["views"].values())
    report["score"] = f"{total - len(report['failed'])}/{total}"
    (out / "design_qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    return report


if __name__ == "__main__":
    r = run(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "qa"))
    print(json.dumps({k: r[k] for k in ("score", "failed")}, ensure_ascii=False, indent=1))
    for v, d in r["views"].items():
        print(v, json.dumps(d["metrics"], ensure_ascii=False))
    sys.exit(1 if r["failed"] else 0)

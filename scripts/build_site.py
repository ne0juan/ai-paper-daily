"""Assemble the deployable static site in dist/:
site/ (UI) + data/ (JSON) + mirrored PDFs (last N days) + feed.xml + _headers."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar.mirror import is_pdf  # noqa: E402

HEADERS = """/data/*
  Cache-Control: public, max-age=300
  Access-Control-Allow-Origin: *
/pdf/*
  Cache-Control: public, max-age=31536000, immutable
  Content-Type: application/pdf
  Content-Disposition: inline
  X-Robots-Tag: noindex
/assets/*
  Cache-Control: public, max-age=3600
/*
  X-Robots-Tag: noindex
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
"""


def rss(items: list[dict], base: str) -> str:
    rows = []
    for it in items[:60]:
        link = f"{base}/#/item/{it['id']}" if base else it["url"]
        desc = escape(f"{it.get('summary_zh', '')} — {it['title']}")
        rows.append(f"<item><title>{escape(it.get('title_zh') or it['title'])}</title><link>{escape(link)}</link>"
                    f"<guid isPermaLink=\"false\">{it['id']}</guid><description>{desc}</description>"
                    f"<pubDate>{it.get('selected_at', '')}</pubDate></item>")
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            "<title>Paper Radar · AI 论文雷达</title>"
            f"<link>{escape(base or '/')}</link><description>每日精选 AI 论文与文章</description>"
            + "".join(rows) + "</channel></rss>")


def build(data_dir: Path, cache_dir: Path, out: Path, base_url: str = "", retain_days: int | None = None) -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text("utf-8"))
    retain = retain_days or cfg.get("selection", {}).get("retain_pdf_days", 21)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    (out / "data" / "days").mkdir(parents=True, exist_ok=True)
    (out / "pdf").mkdir(exist_ok=True)
    cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=retain)).strftime("%Y-%m-%d")
    stats = {"days": 0, "items": 0, "pdfs": 0, "missing_pdf": 0}
    latest: list[dict] = []
    for f in sorted((data_dir / "days").glob("*.json"), reverse=True):
        day = json.loads(f.read_text("utf-8"))
        for it in day["items"]:
            src = cache_dir / Path(it.get("mirror_pdf") or "_").name
            if it.get("mirror_pdf") and day["date"] >= cutoff and is_pdf(src):
                shutil.copy2(src, out / "pdf" / src.name)
                stats["pdfs"] += 1
            elif it.get("mirror_pdf"):
                it["mirror_pdf"] = ""       # expired or lost: fall back to origin links
                stats["missing_pdf"] += 1
            stats["items"] += 1
        latest += day["items"]
        (out / "data" / "days" / f.name).write_text(json.dumps(day, ensure_ascii=False), "utf-8")
        stats["days"] += 1
    idx = data_dir / "index.json"
    index = json.loads(idx.read_text("utf-8")) if idx.exists() else {"days": [], "total": 0, "updated": ""}
    index["build"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "data" / "index.json").write_text(json.dumps(index, ensure_ascii=False), "utf-8")
    latest.sort(key=lambda i: i.get("selected_at", ""), reverse=True)
    (out / "feed.xml").write_text(rss(latest, base_url.rstrip("/")), "utf-8")
    (out / "_headers").write_text(HEADERS, "utf-8")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--cache-dir", default=str(ROOT / ".cache" / "pdf"))
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--base-url", default="")
    a = ap.parse_args()
    print(json.dumps(build(Path(a.data_dir), Path(a.cache_dir), Path(a.out), a.base_url)))

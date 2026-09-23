"""Give already-published items Chinese titles/summaries if they lack them
(e.g. items selected before translation existed, or when MT was down), and refresh
reading links (中文全文 etc.) on older paper items."""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar.http import Http  # noqa: E402
from radar.models import Item  # noqa: E402
from radar.pipeline import build_links  # noqa: E402
from radar.translate import machine_translate  # noqa: E402

logging.basicConfig(level="INFO")
http, done = Http(), 0
for f in sorted((ROOT / "data" / "days").glob("*.json"))[-7:]:
    day = json.loads(f.read_text("utf-8"))
    changed = False
    for d in day["items"]:
        if d["id"].startswith("arxiv-") and not any("hjfy.top" in l["url"] for l in d.get("links", [])):
            d["links"] = build_links(Item.from_dict(d))
            changed = True
    need = [d for d in day["items"] if not d.get("title_zh") and d.get("score_llm") is None]
    items = [Item.from_dict(d) for d in need]
    for it in items:
        it.summary_zh = ""
    if items and machine_translate(http, items):
        for d, it in zip(need, items):
            if it.title_zh:
                d.update(title_zh=it.title_zh, summary_zh=it.summary_zh or d.get("summary_zh", ""), mt=True)
                done += 1
                changed = True
    if changed:
        f.write_text(json.dumps(day, ensure_ascii=False, indent=1), "utf-8")
print(f"backfill_zh: translated {done}")

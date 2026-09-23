"""Live health check of every source. Records raw responses (for refreshing test
fixtures) and writes probe/summary.json. Run in CI: python scripts/probe.py out_dir"""
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar import sources  # noqa: E402
from radar.http import Http  # noqa: E402


def main(out: Path) -> int:
    logging.basicConfig(level="INFO")
    cfg = yaml.safe_load((ROOT / "config/sources.yaml").read_text("utf-8"))
    http = Http(record_dir=str(out / "raw"))
    now = datetime.now(timezone.utc)
    report = {"at": now.isoformat(), "sources": {}}

    def probe(name, fn):
        t = time.time()
        items = fn()
        report["sources"][name] = {"n": len(items), "secs": round(time.time() - t, 1),
                                   "sample": [f"{i.id} | {i.title[:90]}" for i in items[:4]]}

    probe("hf_daily", lambda: sources.fetch_hf_daily(http, cfg["hf_daily"], now))
    probe("hackernews", lambda: sources.fetch_hackernews(http, cfg["hackernews"], now))
    for f in cfg["rss"]:
        probe("rss:" + f["name"], lambda f=f: sources.fetch_rss(http, [f], now, 24 * 30))
    for L in cfg["html_lists"]:
        probe("list:" + L["url"], lambda L=L: sources.fetch_html_lists(http, [L], {}, now))
    probe("arxiv_enrich", lambda: (lambda it: (sources.enrich_arxiv(http, [it]), [it] if it.abstract else [])[1])(
        (lambda i: (i.extra.__setitem__("needs_arxiv_meta", True), i)[1])(sources.paper_item("1706.03762", "x"))))
    for raw in (out / "raw").glob("*"):
        if raw.stat().st_size > 400_000:
            raw.unlink()
    (out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    dead = [k for k, v in report["sources"].items() if v["n"] == 0]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("DEAD:", dead)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "probe")))

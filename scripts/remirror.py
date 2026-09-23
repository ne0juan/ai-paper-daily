"""If the Actions cache was evicted, re-download PDFs for items still inside the
retention window so the site never shows a dead 本站镜像 link (paper PDFs only;
article snapshots that are lost simply fall back to origin links)."""
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar.http import Http  # noqa: E402
from radar.mirror import is_pdf, mirror_pdf  # noqa: E402
from radar.models import Item  # noqa: E402

logging.basicConfig(level="INFO")
cfg = yaml.safe_load((ROOT / "config/sources.yaml").read_text("utf-8"))
days = cfg["selection"].get("retain_pdf_days", 21)
cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)).strftime("%Y-%m-%d")
cache = ROOT / ".cache" / "pdf"
http, fixed, missing = Http(), 0, 0
for f in sorted((ROOT / "data" / "days").glob("*.json")):
    day = json.loads(f.read_text("utf-8"))
    if day["date"] < cutoff:
        continue
    for d in day["items"]:
        if d.get("mirror_pdf") and not is_pdf(cache / Path(d["mirror_pdf"]).name):
            missing += 1
            if d.get("pdf_url") and mirror_pdf(http, Item.from_dict(d), cache):
                fixed += 1
print(f"remirror: missing={missing} fixed={fixed}")

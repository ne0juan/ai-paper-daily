"""Pre-deploy gate: fail loudly if the built site is broken or breaks Pages limits."""
from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_FILE = 25 * 1024 * 1024
MAX_FILES = 20000
REQUIRED = ["id", "kind", "title", "url", "score", "date", "slot", "links", "sources"]


def verify(dist: Path) -> list[str]:
    errs = []
    for req in ["index.html", "app.js", "style.css", "data/index.json", "_headers", "feed.xml"]:
        if not (dist / req).is_file():
            errs.append(f"missing {req}")
    files = [p for p in dist.rglob("*") if p.is_file()]
    if len(files) > MAX_FILES:
        errs.append(f"too many files: {len(files)}")
    errs += [f"file too large: {p}" for p in files if p.stat().st_size > MAX_FILE]
    try:
        index = json.loads((dist / "data/index.json").read_text("utf-8"))
    except Exception as e:  # noqa: BLE001
        return errs + [f"index.json unreadable: {e}"]
    ids = set()
    for d in index.get("days", []):
        f = dist / "data" / "days" / f"{d['date']}.json"
        if not f.is_file():
            errs.append(f"index lists missing day {d['date']}")
            continue
        day = json.loads(f.read_text("utf-8"))
        if len(day["items"]) != d["count"]:
            errs.append(f"{d['date']}: count mismatch {len(day['items'])} != {d['count']}")
        for it in day["items"]:
            miss = [k for k in REQUIRED if k not in it]
            if miss:
                errs.append(f"{it.get('id')}: missing fields {miss}")
            if it["id"] in ids:
                errs.append(f"duplicate item {it['id']}")
            ids.add(it["id"])
            if it.get("mirror_pdf"):
                p = dist / it["mirror_pdf"]
                if not p.is_file() or p.read_bytes()[:5] != b"%PDF-":
                    errs.append(f"{it['id']}: broken mirror {it['mirror_pdf']}")
            for l in it.get("links", []):
                if not str(l.get("url", "")).startswith(("https://", "http://")):
                    errs.append(f"{it['id']}: bad link {l}")
    return errs


if __name__ == "__main__":
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    errors = verify(dist)
    for e in errors:
        print("ERROR", e)
    print(f"verify_dist: {'FAILED' if errors else 'OK'} ({len(errors)} errors)")
    sys.exit(1 if errors else 0)

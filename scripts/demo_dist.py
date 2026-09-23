"""Build a demo dist from tests/fixtures/demo_day.json (realistic Chinese content) for design QA."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_site import build  # noqa: E402

subprocess.run([sys.executable, str(ROOT / "scripts/make_demo_fixture.py")], check=True, capture_output=True)
out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / ".qa-dist")
work = out.parent / (out.name + "-src")
shutil.rmtree(work, ignore_errors=True)
(work / "data/days").mkdir(parents=True)
(work / "cache").mkdir()
day = json.loads((ROOT / "tests/fixtures/demo_day.json").read_text("utf-8"))
(work / "data/days/2026-09-23.json").write_text(json.dumps(day, ensure_ascii=False), "utf-8")
for it in day["items"]:
    if it["mirror_pdf"]:
        shutil.copy(ROOT / "tests/fixtures/tiny.pdf", work / "cache" / Path(it["mirror_pdf"]).name)
idx = {"updated": "2026-09-23T11:05:00+00:00", "days": [{"date": "2026-09-23", "count": len(day["items"])}],
       "total": len(day["items"]), "last_run": {"at": "2026-09-23T11:05:00+00:00", "candidates": 91, "selected": 8, "mirrored": 7}}
(work / "data/index.json").write_text(json.dumps(idx, ensure_ascii=False), "utf-8")
print(build(work / "data", work / "cache", out, retain_days=3650))

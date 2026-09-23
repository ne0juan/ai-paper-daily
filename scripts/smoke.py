"""Post-deploy smoke test against the live URL: the new build is served, the app shell
loads, and a mirrored PDF is downloadable as application/pdf."""
import json
import sys
import time
from pathlib import Path

import requests


def main(base: str, dist: Path) -> int:
    want = json.loads((dist / "data/index.json").read_text())["build"]
    ok = {}
    for _ in range(24):
        try:
            idx = requests.get(f"{base}/data/index.json", timeout=20, headers={"Cache-Control": "no-cache"}).json()
            if idx.get("build") == want:
                break
        except Exception as e:  # noqa: BLE001
            print("waiting:", e)
        time.sleep(10)
    else:
        print("FAIL: live index.json never showed build", want)
        return 1
    ok["index"] = True
    html = requests.get(base + "/", timeout=20).text
    ok["shell"] = "Paper Radar" in html and "app.js" in html
    js = requests.get(base + "/app.js", timeout=20)
    ok["appjs"] = js.status_code == 200 and "boot" in js.text
    pdf_path = None
    for d in idx.get("days", [])[:3]:
        day = requests.get(f"{base}/data/days/{d['date']}.json", timeout=20).json()
        pdf_path = next((i["mirror_pdf"] for i in day["items"] if i.get("mirror_pdf")), None)
        if pdf_path:
            break
    if pdf_path:
        r = requests.get(f"{base}/{pdf_path}", timeout=60)
        ok["pdf"] = r.status_code == 200 and r.content[:5] == b"%PDF-" and "pdf" in r.headers.get("content-type", "")
    print(json.dumps(ok))
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/"), Path(sys.argv[2] if len(sys.argv) > 2 else "dist")))

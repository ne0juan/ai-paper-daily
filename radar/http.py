"""Tiny HTTP layer: retries, per-host politeness, optional response recording.

Sources only talk to the network through an ``Http`` instance, so tests can
inject ``FakeHttp`` with canned fixtures and the probe workflow can record
real responses (RADAR_RECORD_DIR) to refresh those fixtures.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 PaperRadar/1.0 (+internal research digest)")

# arXiv asks for >=3s between requests.
HOST_DELAY = {"export.arxiv.org": 3.1, "arxiv.org": 1.5}


class HttpError(RuntimeError):
    pass


class Http:
    def __init__(self, record_dir: str | None = None, timeout: float = 30):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
        self.timeout = timeout
        self._last: dict[str, float] = {}
        rd = record_dir or os.environ.get("RADAR_RECORD_DIR")
        self.record_dir = Path(rd) if rd else None

    def _wait(self, host: str) -> None:
        delay = HOST_DELAY.get(host, 0.3)
        last = self._last.get(host, 0)
        gap = time.time() - last
        if gap < delay:
            time.sleep(delay - gap)
        self._last[host] = time.time()

    def get(self, url: str, *, headers: dict | None = None, params: dict | None = None,
            retries: int = 3, stream: bool = False) -> requests.Response:
        host = urlsplit(url).netloc
        err: Exception | None = None
        for attempt in range(retries):
            self._wait(host)
            try:
                r = self.s.get(url, headers=headers, params=params, timeout=self.timeout, stream=stream)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise HttpError(f"{r.status_code} for {url}")
                if r.status_code >= 400:
                    raise HttpError(f"{r.status_code} for {url}")  # not retried below
                return r
            except (requests.RequestException, HttpError) as e:
                err = e
                if isinstance(e, HttpError) and not re.match(r"^(429|5\d\d)", str(e)):
                    break
                time.sleep(2 * (attempt + 1))
        raise HttpError(str(err))

    def _record(self, url: str, body: str | bytes) -> None:
        if not self.record_dir:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9]+", "_", urlsplit(url).netloc + urlsplit(url).path)[:80]
        name += "_" + hashlib.md5(url.encode()).hexdigest()[:6]
        data = body.encode() if isinstance(body, str) else body
        (self.record_dir / name).write_bytes(data)
        idx = self.record_dir / "_index.jsonl"
        with idx.open("a") as f:
            f.write(json.dumps({"url": url, "file": name}) + "\n")

    def text(self, url: str, **kw) -> str:
        r = self.get(url, **kw)
        r.encoding = r.encoding or "utf-8"
        body = r.text
        self._record(r.url, body)
        return body

    def json(self, url: str, **kw):
        return json.loads(self.text(url, **kw))

    def post_json(self, url: str, *, body, params: dict | None = None, headers: dict | None = None):
        host = urlsplit(url).netloc
        self._wait(host)
        r = self.s.post(url, json=body, params=params, headers=headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise HttpError(f"{r.status_code} for {url}")
        return r.json()

    def download(self, url: str, dest: Path, max_bytes: int, **kw) -> int:
        """Stream a file to disk; abort if larger than max_bytes. Returns size."""
        r = self.get(url, stream=True, **kw)
        size = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("wb") as f:
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    raise HttpError(f"too large (> {max_bytes} bytes): {url}")
                f.write(chunk)
        tmp.replace(dest)
        return size


class FakeHttp:
    """Test double: maps URL substrings to fixture bodies (str/bytes/Exception)."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def _match(self, url: str):
        self.calls.append(url)
        for key, val in self.routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise HttpError(f"404 (fake) for {url}")

    def text(self, url: str, **kw) -> str:
        val = self._match(url + ("?" + "&".join(f"{k}={v}" for k, v in kw.get("params", {}).items())
                                 if kw.get("params") else ""))
        return val.decode() if isinstance(val, bytes) else str(val)

    def json(self, url: str, **kw):
        return json.loads(self.text(url, **kw))

    def post_json(self, url: str, *, body, params: dict | None = None, headers: dict | None = None):
        val = self._match(url)
        return val(body) if callable(val) else json.loads(val)

    def download(self, url: str, dest: Path, max_bytes: int, **kw) -> int:
        val = self._match(url)
        data = val if isinstance(val, bytes) else str(val).encode()
        if len(data) > max_bytes:
            raise HttpError("too large")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)

"""Free machine translation (EN -> ZH) used when no LLM key is configured, so Chinese
readers still get Chinese titles and summaries. Uses the public Google Translate web
endpoint; any failure just leaves the English text in place."""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)
ENDPOINT = "https://translate.googleapis.com/translate_a/single"
CJK = re.compile("[\\u4e00-\\u9fff]")


def translate(http, text: str) -> str:
    text = (text or "").strip()
    if not text or CJK.search(text):
        return text
    data = http.json(ENDPOINT, params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text[:1800]})
    out = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
    return out.strip()


def lead(abstract: str, n: int = 2, limit: int = 360) -> str:
    """First n sentences of an abstract, capped."""
    parts = re.split(r"(?<=[.!?])\s+", (abstract or "").strip())
    return " ".join(parts[:n])[:limit]


def machine_translate(http, items) -> int:
    """Fill title_zh / summary_zh for items the LLM did not enrich. Returns #translated."""
    ok = 0
    for it in items:
        if it.score_llm is not None:
            continue
        try:
            zh_title = translate(http, it.title)
            zh_sum = translate(http, lead(it.abstract)) if it.abstract else ""
        except Exception as e:  # noqa: BLE001
            log.warning("mt failed for %s: %s", it.id, e)
            continue
        if zh_title and zh_title != it.title:
            it.title_zh = zh_title
        if zh_sum:
            it.summary_zh = zh_sum
        it.mt = True
        ok += 1
    return ok

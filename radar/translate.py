"""Free machine translation (EN -> ZH) used when no LLM key is configured, so Chinese
readers still get Chinese titles and summaries. No API key needed.

Providers are tried in order; a provider that fails is skipped for the rest of the run:
  1. Microsoft Edge translator (batch, works from cloud runners)
  2. Google Translate web endpoint (often rate-limited on datacenter IPs)
Any total failure leaves the English text in place."""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)
CJK = re.compile("[\\u4e00-\\u9fff]")
EDGE_AUTH = "https://edge.microsoft.com/translate/auth"
EDGE_API = "https://api-edge.cognitivetranslator.com/translate"
GOOGLE = "https://translate.googleapis.com/translate_a/single"


def _edge(http, texts: list[str]) -> list[str]:
    token = http.text(EDGE_AUTH).strip()
    out: list[str] = []
    for i in range(0, len(texts), 25):
        chunk = texts[i:i + 25]
        data = http.post_json(EDGE_API, params={"from": "en", "to": "zh-Hans", "api-version": "3.0"},
                              headers={"Authorization": f"Bearer {token}"},
                              body=[{"Text": t} for t in chunk])
        out += [row["translations"][0]["text"] for row in data]
    return out


def _google(http, texts: list[str]) -> list[str]:
    out = []
    for t in texts:
        data = http.json(GOOGLE, params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": t})
        out.append("".join(seg[0] for seg in (data[0] or []) if seg and seg[0]).strip())
    return out


PROVIDERS = [("edge", _edge), ("google", _google)]


def translate_many(http, texts: list[str]) -> list[str]:
    """Translate a batch; Chinese/empty strings pass through untouched."""
    idx = [i for i, t in enumerate(texts) if t and t.strip() and not CJK.search(t)]
    if not idx:
        return list(texts)
    todo = [texts[i].strip()[:1800] for i in idx]
    for name, fn in PROVIDERS:
        try:
            res = fn(http, todo)
            if len(res) == len(todo) and all(res):
                out = list(texts)
                for i, r in zip(idx, res):
                    out[i] = r
                log.info("mt: %d strings via %s", len(todo), name)
                return out
        except Exception as e:  # noqa: BLE001
            log.warning("mt provider %s failed: %s", name, e)
    raise RuntimeError("all translation providers failed")


def translate(http, text: str) -> str:
    return translate_many(http, [text])[0]


def lead(abstract: str, n: int = 2, limit: int = 360) -> str:
    """First n sentences of an abstract, capped."""
    text = (abstract or "").strip()
    if CJK.search(text):
        parts = re.split(r"(?<=[。！？])", text)
        return "".join(parts[:n])[:limit]
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n])[:limit]


def machine_translate(http, items) -> int:
    """Fill title_zh / summary_zh for items the LLM did not enrich. Returns #translated."""
    todo = [it for it in items if it.score_llm is None]
    if not todo:
        return 0
    texts = []
    for it in todo:
        texts += [it.title, lead(it.abstract) if it.abstract else ""]
    try:
        zh = translate_many(http, texts)
    except Exception as e:  # noqa: BLE001
        log.warning("mt failed: %s", e)
        return 0
    for k, it in enumerate(todo):
        t, s = zh[2 * k], zh[2 * k + 1]
        if t and t != it.title:
            it.title_zh = t
        if s:
            it.summary_zh = s
        it.mt = True
    return len(todo)

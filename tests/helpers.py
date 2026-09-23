import json
from datetime import datetime, timezone
from pathlib import Path

from radar.http import FakeHttp, HttpError

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)   # 11:00 Beijing


def fx(name: str, mode: str = "r"):
    return (FIX / name).read_bytes() if mode == "rb" else (FIX / name).read_text("utf-8")


def fake_http(**overrides) -> FakeHttp:
    routes = {
        "huggingface.co/api/daily_papers?date=2026-09-23": fx("hf_daily.json"),
        "huggingface.co/api/daily_papers": "[]",
        "hn.algolia.com": fx("hn.json"),
        "export.arxiv.org/api/query": fx("arxiv_api.xml"),
        "openai.com/news/rss.xml": fx("openai_rss.xml"),
        "lilianweng.github.io/index.xml": fx("lilian_atom.xml"),
        "anthropic.com/research/tracing-model-thoughts": fx("anthropic_article.html"),
        "anthropic.com/research/constitutional-classifiers-v2": fx("anthropic_article.html").replace(
            "Tracing the thoughts of a model", "Constitutional Classifiers v2"),
        "www.anthropic.com/research": fx("anthropic_list.html"),
        "arxiv.org/pdf/": fx("tiny.pdf", "rb"),
    }
    routes.update(overrides)
    return FakeHttp(routes)


def fake_llm_transport(system: str, user: str) -> str:
    """Deterministic judge: parses ids from the prompt; marks bread as irrelevant."""
    rows = []
    for block in user.split("---"):
        lines = {l.split(": ", 1)[0].strip(): l.split(": ", 1)[1] for l in block.strip().splitlines() if ": " in l}
        iid = lines.get("id")
        if not iid:
            continue
        title = lines.get("标题", "")
        bread = "sourdough" in title.lower()
        rows.append({"id": iid, "relevant": not bread, "quality": 3 if bread else 8,
                     "title_zh": "中文：" + title[:20], "summary_zh": "一句话摘要。",
                     "highlights_zh": ["要点一", "要点二", "要点三"], "why_zh": "值得一读。",
                     "tags": ["推理", "不存在的标签"]})
    return "```json\n" + json.dumps(rows, ensure_ascii=False) + "\n```"


__all__ = ["FIX", "NOW", "fx", "fake_http", "fake_llm_transport", "HttpError"]

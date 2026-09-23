import json
from datetime import datetime, timezone
from pathlib import Path

from radar.http import FakeHttp, HttpError

FIX = Path(__file__).parent / "fixtures"
TEST_CONFIG = FIX / "_test_sources.yaml"


def _write_test_config() -> None:
    """The real config, pointed at the offline fixtures (regenerated on import, git-ignored)."""
    import yaml
    c = yaml.safe_load((FIX.parent.parent / "config" / "sources.yaml").read_text("utf-8"))
    c["hf_daily"]["min_upvotes"] = 3
    c["hackernews"]["min_points"] = 60
    c["hackernews"]["keywords"] += ["transformer", "arxiv"]
    c["rss"] = [
        {"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "authority": .9, "kind": "official", "lang": "en"},
        {"name": "Lil'Log", "url": "https://lilianweng.github.io/index.xml", "authority": .9, "kind": "opinion",
         "lang": "en", "who": "OpenAI 研究员", "who_name": "Lilian Weng"}]
    c["html_lists"] = [{"name": "Anthropic", "url": "https://www.anthropic.com/research", "base": "https://www.anthropic.com",
                        "pattern": "^/(research|news)/[a-z0-9-]{6,}$", "authority": .9}]
    c["rss_lookback_hours"] = 72
    c["github"]["trending_paths"] = [""]
    TEST_CONFIG.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), "utf-8")


_write_test_config()
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
        "github.com/trending": fx("gh_trending.html"),
        "api.github.com/search": fx("gh_search.json"),
        "edge.microsoft.com/translate/auth": "fake-jwt",
        "api-edge.cognitivetranslator.com": lambda body: [{"translations": [{"text": "机器译文", "to": "zh-Hans"}]} for _ in body],
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


__all__ = ["TEST_CONFIG", "FIX", "NOW", "fx", "fake_http", "fake_llm_transport", "HttpError"]

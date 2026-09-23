"""Rule-based authority/quality score and final selection."""
from __future__ import annotations

from datetime import datetime, timezone

from .feeds import parse_date
from .models import Item
from .sources import popularity

# Organisations whose papers get a small authority boost.
TOP_ORGS = [
    "openai", "anthropic", "deepmind", "google", "meta", "fair", "microsoft", "nvidia", "apple",
    "stanford", "berkeley", "mit", "cmu", "carnegie mellon", "princeton", "tsinghua", "peking",
    "alibaba", "qwen", "deepseek", "moonshot", "bytedance", "seed", "tencent", "shanghai ai lab",
    "allen institute", "ai2", "mistral", "hugging face", "eth zurich", "oxford", "cambridge",
]

POP_SCALE = {"HF Daily Papers": 150, "Hacker News": 600}


def rule_score(it: Item, now: datetime) -> float:
    if not it.sources:
        return 0.0
    authority = max(s.authority for s in it.sources)
    pops = [popularity(s.metric, POP_SCALE.get(s.name, 2000 if s.name.startswith("X @") else 1))
            for s in it.sources if s.metric]
    pop = max(pops) if pops else 0.0
    distinct = len({s.name.split(" @")[0] for s in it.sources})
    cross = min(1.0, (distinct - 1) / 2)          # seen on 2+ independent sources
    org_text = " ".join(it.orgs + it.authors[:3]).lower()
    org = 1.0 if any(o in org_text for o in TOP_ORGS) else 0.0
    pub = parse_date(it.published)
    age_h = (now - pub).total_seconds() / 3600 if pub else 24
    fresh = 1.0 if age_h <= 48 else max(0.0, 1 - (age_h - 48) / 120)
    s = 0.40 * authority + 0.30 * pop + 0.12 * cross + 0.08 * org + 0.10 * fresh
    return round(min(1.0, s), 4)


def final_score(it: Item) -> float:
    if it.score_llm is None:
        return it.score_rule
    return round(0.4 * it.score_rule + 0.6 * (it.score_llm / 10), 4)


def select(items: list[Item], per_run: int, min_score: float, max_per_source: int,
           zh_ratio: float | None = None, papers_left: int = 0) -> list[Item]:
    """Pick the issue: best non-paper items with a Chinese quota, plus at most
    ``papers_left`` papers (the hottest), with a per-source cap for diversity."""
    ranked = sorted((i for i in items if i.relevant and i.score >= min_score),
                    key=lambda i: i.score, reverse=True)
    per_src: dict[str, int] = {}

    def take(pool, limit, out):
        for it in pool:
            if len(out) >= limit:
                break
            src = it.sources[0].name if it.sources else "?"
            if it in out or per_src.get(src, 0) >= max_per_source:
                continue
            out.append(it)
            per_src[src] = per_src.get(src, 0) + 1
        return out

    papers = [i for i in ranked if i.category == "论文"]
    rest = [i for i in ranked if i.category != "论文"]
    picked: list[Item] = []
    if zh_ratio is None:
        take(rest, per_run, picked)
    else:
        zh_quota = round(per_run * zh_ratio)
        take([i for i in rest if i.lang == "zh"], zh_quota, picked)
        take([i for i in rest if i.lang != "zh"], per_run, picked)
        take(rest, per_run, picked)          # top up if one language is short
    papers = sorted(papers, key=lambda i: max((s.metric for s in i.sources), default=0), reverse=True)
    take(papers, len(picked) + max(0, papers_left), picked)
    return sorted(picked, key=lambda i: i.score, reverse=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

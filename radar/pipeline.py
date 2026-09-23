"""End-to-end run: collect -> score -> LLM judge -> select -> mirror -> store.

    python -m radar.pipeline [--slot morning|noon|evening] [--no-llm] [--no-snapshots]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import sources
from .http import Http
from .llm import LLM
from .mirror import mirror_all
from .translate import lead, machine_translate
from .models import Item
from .scoring import final_score, rule_score, select

log = logging.getLogger("radar")
BJ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
SLOTS = {"morning": "晨间", "noon": "午间", "evening": "晚间"}

KEYWORD_TAGS = [
    ("智能体", r"\bagent"), ("多模态", r"multimodal|vision-language|\bvlm\b|video|image"),
    ("推理", r"reason|chain-of-thought|\bmath"), ("强化学习", r"reinforcement|\brl\b|reward"),
    ("对齐与安全", r"align|safety|jailbreak|interpretab"), ("效率与系统", r"efficien|quantiz|inference|kernel|serving|sparse|moe\b"),
    ("代码", r"\bcode|coding|software"), ("机器人", r"robot|embodied"), ("语音", r"speech|audio|tts\b"),
    ("数据与评测", r"benchmark|dataset|evaluat"), ("大模型", r"language model|\bllm|gpt|transformer"),
]


def slot_for(now: datetime) -> str:
    h = now.astimezone(BJ).hour
    return "morning" if h < 11 else "noon" if h < 17 else "evening"


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def dump_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(p)


def heuristic_enrich(it: Item) -> None:
    """Used when the LLM is disabled or failed for an item."""
    text = f"{it.title} {it.abstract}".lower()
    if not it.tags:
        it.tags = [t for t, pat in KEYWORD_TAGS if re.search(pat, text)][:3] or ["大模型"]
    if not it.summary_zh and it.abstract:
        it.summary_zh = lead(it.abstract, 2, 160 if it.lang == "zh" else 360)


def build_links(it: Item) -> list[dict]:
    links = []
    if it.arxiv_id:
        a = it.arxiv_id
        links += [{"label": "中文全文（幻觉翻译）", "url": f"https://hjfy.top/arxiv/{a}"},
                  {"label": "中文解读（Cool Papers）", "url": f"https://papers.cool/arxiv/{a}"},
                  {"label": "arXiv", "url": f"https://arxiv.org/abs/{a}"},
                  {"label": "arXiv PDF", "url": f"https://arxiv.org/pdf/{a}"},
                  {"label": "alphaXiv 讨论", "url": f"https://www.alphaxiv.org/abs/{a}"},
                  {"label": "HF Papers", "url": f"https://huggingface.co/papers/{a}"}]
    else:
        links.append({"label": "原文", "url": it.url})
        if it.pdf_url and it.pdf_url != it.url:
            links.append({"label": "原站 PDF", "url": it.pdf_url})
    if it.extra.get("github"):
        links.append({"label": "GitHub", "url": it.extra["github"]})
    for s in it.sources:
        if s.name == "Hacker News" or s.name.startswith("X @"):
            links.append({"label": f"{s.name} 讨论", "url": s.url})
    return links


def update_index(data_dir: Path, now: datetime, run_info: dict) -> dict:
    days = []
    for f in sorted((data_dir / "days").glob("*.json"), reverse=True):
        d = load_json(f, {})
        c = Counter(i.get("slot") for i in d.get("items", []))
        days.append({"date": d.get("date", f.stem), "count": len(d.get("items", [])), "slots": dict(c)})
    idx = {"updated": now.isoformat(timespec="seconds"), "days": days,
           "total": sum(d["count"] for d in days), "last_run": run_info,
           "slots": SLOTS}
    dump_json(data_dir / "index.json", idx)
    return idx


def run(*, now: datetime | None = None, slot: str | None = None, data_dir: Path = ROOT / "data",
        cache_dir: Path = ROOT / ".cache" / "pdf", config: Path = ROOT / "config" / "sources.yaml",
        http=None, llm: LLM | None = None, snapshots: bool = True, translate: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    slot = slot or slot_for(now)
    cfg = yaml.safe_load(config.read_text("utf-8"))
    sel = cfg.get("selection", {})
    http = http or Http()
    llm = llm if llm is not None else LLM()
    state = load_json(data_dir / "state.json", {"selected": {}, "known_links": {}, "runs": []})

    # 1. collect
    items = sources.collect(http, cfg, state, now)
    by_source = Counter(s.name.split(" @")[0] for it in items for s in it.sources)
    fresh = [it for it in items if it.id not in state["selected"]]
    log.info("candidates: %d (new %d)", len(items), len(fresh))

    # 2. rule score, 3. LLM judge on the top slice
    for it in fresh:
        it.score_rule = rule_score(it, now)
        it.score = it.score_rule
    fresh.sort(key=lambda i: i.score_rule, reverse=True)
    top = fresh[: sel.get("llm_candidates", 40)]
    judged = 0
    if llm.enabled and top:
        judged = llm.judge(top)
        log.info("llm judged %d/%d (calls=%d)", judged, len(top), llm.calls)
    for it in top:
        it.score = final_score(it)
        heuristic_enrich(it)

    # 4. select (Chinese quota; papers: only the hottest, ≤ papers_per_day per day, ≤ 1 per issue)
    bj_date = now.astimezone(BJ).strftime("%Y-%m-%d")
    day_file = data_dir / "days" / f"{bj_date}.json"
    day = load_json(day_file, {"date": bj_date, "items": []})
    papers_today = sum(1 for i in day["items"] if i.get("category") == "论文" or i.get("kind") == "paper")
    papers_left = min(1, max(0, sel.get("papers_per_day", 2) - papers_today))
    picked = select(top, sel.get("per_run", 10), sel.get("min_score", 0.35), sel.get("max_per_source", 3),
                    zh_ratio=sel.get("zh_ratio"), papers_left=papers_left, min_opinions=sel.get("min_opinions", 0))
    for it in picked:
        it.date, it.slot = bj_date, slot
        it.selected_at = now.isoformat(timespec="seconds")
        it.links = build_links(it)

    # 5. Chinese for Chinese readers: machine translation where the LLM didn't write it
    translated = machine_translate(http, picked) if translate else 0

    # 6. mirror originals
    mirror_all(http, picked, cache_dir, snapshots=snapshots)

    # 7. store
    have = {i["id"] for i in day["items"]}
    day["items"] += [it.to_dict() for it in picked if it.id not in have]
    day["items"].sort(key=lambda i: ({"morning": 0, "noon": 1, "evening": 2}.get(i["slot"], 3), -i["score"]))
    if picked and llm.enabled:
        try:
            day["brief"] = llm.daily_brief(day["items"])
        except Exception as e:  # noqa: BLE001
            log.warning("daily brief failed: %s", e)
    if picked:
        dump_json(day_file, day)
    for it in picked:
        state["selected"][it.id] = bj_date
    # forget selections older than 60 days so the state file stays small
    cutoff = (now - timedelta(days=60)).astimezone(BJ).strftime("%Y-%m-%d")
    state["selected"] = {k: v for k, v in state["selected"].items() if v >= cutoff}
    run_info = {"at": now.isoformat(timespec="seconds"), "slot": slot, "candidates": len(items),
                "new": len(fresh), "judged": judged, "selected": len(picked),
                "mirrored": sum(1 for i in picked if i.mirror_pdf), "by_source": dict(by_source),
                "llm": llm.enabled, "translated": translated}
    state["runs"] = (state.get("runs", []) + [run_info])[-60:]
    dump_json(data_dir / "state.json", state)
    update_index(data_dir, now, run_info)
    log.info("run done: %s", json.dumps(run_info, ensure_ascii=False))
    return run_info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=list(SLOTS))
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-snapshots", action="store_true")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--cache-dir", default=str(ROOT / ".cache" / "pdf"))
    a = ap.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    if a.no_llm:
        os.environ.pop("LLM_API_KEY", None)
    llm = LLM()
    info = run(slot=a.slot, data_dir=Path(a.data_dir), cache_dir=Path(a.cache_dir), llm=llm,
               snapshots=not a.no_snapshots)
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a") as f:
            f.write("### Paper Radar run\n```json\n" + json.dumps(info, ensure_ascii=False, indent=1) + "\n```\n")
    if info["candidates"] == 0:
        raise SystemExit("no candidates at all — every source failed?")


if __name__ == "__main__":
    main()

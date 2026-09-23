"""LLM judge + Chinese summariser. Works with Anthropic or any OpenAI-compatible API
(OpenAI, DeepSeek, Moonshot, Qwen/DashScope, 智谱 ...).

Env:
  LLM_PROVIDER  anthropic | openai            (default: anthropic if key looks like sk-ant-, else openai)
  LLM_API_KEY   the key
  LLM_BASE_URL  optional, OpenAI-compatible base (e.g. https://api.deepseek.com/v1)
  LLM_MODEL     model name
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable

import requests

from .models import Item

log = logging.getLogger(__name__)

TAGS = ["大模型", "多模态", "智能体", "推理", "对齐与安全", "视觉", "语音", "机器人", "强化学习",
        "效率与系统", "理论", "数据与评测", "代码", "科学AI", "产品与行业"]

SYSTEM = f"""你是一家科技公司 AI 研究情报编辑，负责从候选中挑出最权威、最有价值的 AI 论文和文章，并为中文读者撰写精炼摘要。
评分标准（quality 1-10）：
- 9-10：领域里程碑/顶级实验室重要发布/可能改变实践的方法，证据扎实
- 7-8：扎实的新方法或重要实证发现，来源可靠，工程师值得读
- 5-6：有一定价值但增量、或偏窄
- 1-4：营销软文、观点水文、与 AI 研究关系弱、标题党
relevant：是否属于 AI/机器学习的研究、工程或重要行业进展（纯商业八卦/政治/非 AI 为 false）。
tags 只能从以下选择 1-3 个：{"、".join(TAGS)}
输出严格 JSON 数组，不要任何多余文字。每个元素：
{{"id": "...", "relevant": true, "quality": 7, "title_zh": "中文标题（信达雅，≤30字）",
 "summary_zh": "一句话说清做了什么、结果如何（≤70字）", "highlights_zh": ["要点1","要点2","要点3"],
 "why_zh": "为什么值得读（≤40字）", "tags": ["大模型"]}}"""


def _fmt(it: Item) -> str:
    srcs = "; ".join(f"{s.name} {s.signal}".strip() for s in it.sources)
    return (f"id: {it.id}\n类型: {'论文' if it.kind == 'paper' else '文章'}\n标题: {it.title}\n"
            f"作者/机构: {', '.join((it.orgs + it.authors)[:6])}\n来源与热度: {srcs}\n"
            f"摘要: {it.abstract[:1400]}")


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


class LLM:
    def __init__(self, provider: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, model: str | None = None,
                 transport: Callable[[str, str], str] | None = None):
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.provider = (provider or os.environ.get("LLM_PROVIDER")
                         or ("anthropic" if self.api_key.startswith("sk-ant-") else "openai")).lower()
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or
                         ("https://api.anthropic.com" if self.provider == "anthropic"
                          else "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or (
            "claude-sonnet-4-5" if self.provider == "anthropic" else "gpt-4o-mini")
        self.transport = transport  # tests inject a fake (system, user) -> text
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return bool(self.transport or self.api_key)

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if self.transport:
            return self.transport(system, user)
        if self.provider == "anthropic":
            r = requests.post(f"{self.base_url}/v1/messages", timeout=120, headers={
                "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.model, "max_tokens": 4096, "system": system,
                      "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        r = requests.post(f"{self.base_url}/chat/completions", timeout=120, headers={
            "Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={"model": self.model, "temperature": 0.2,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def judge(self, items: list[Item], batch: int = 8) -> int:
        """Enrich items in place. Returns number of items successfully judged."""
        ok = 0
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            user = "请评估以下候选：\n\n" + "\n\n---\n\n".join(_fmt(it) for it in chunk)
            for attempt in range(2):
                try:
                    rows = extract_json(self.complete(SYSTEM, user))
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("llm batch %d attempt %d failed: %s", i // batch, attempt, e)
                    rows = []
            by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
            for it in chunk:
                r = by_id.get(it.id)
                if not r:
                    continue
                try:
                    it.score_llm = max(1.0, min(10.0, float(r.get("quality", 5))))
                except (TypeError, ValueError):
                    continue
                it.relevant = bool(r.get("relevant", True))
                it.title_zh = str(r.get("title_zh", ""))[:80]
                it.summary_zh = str(r.get("summary_zh", ""))[:200]
                it.highlights_zh = [str(h)[:120] for h in (r.get("highlights_zh") or [])][:4]
                it.why_zh = str(r.get("why_zh", ""))[:120]
                it.tags = [t for t in (r.get("tags") or []) if t in TAGS][:3]
                ok += 1
        return ok

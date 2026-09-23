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

SYSTEM = f"""你是一家科技媒体的 AI 行业主编，为中文读者从候选中挑出最能反映 AI 行业趋势与市场方向的内容，并撰写精炼中文摘要。
评分标准（quality 1-10）：
- 9-10：重量级人物的明确判断、头部公司的重大发布或战略变化、可能改变行业格局的事件
- 7-8：有信息增量的行业动态、深度分析、扎实的新技术进展
- 5-6：常规新闻、增量信息
- 1-4：营销软文、融资通稿、标题党、与 AI 关系弱、重复报道
relevant：是否属于 AI/机器学习的研究、工程或重要行业进展（纯商业八卦/政治/非 AI 为 false）。
tags 只能从以下选择 1-3 个：{"、".join(TAGS)}
读者是中文科技从业者，目的是把握 AI 行业趋势与市场方向：
- 观点类（个人博客/访谈）：summary_zh 写成「某某认为……」，提炼其核心判断，而不是复述文章结构；
- 资讯类：summary_zh 讲清发生了什么、影响谁；
- 论文类：summary_zh 用通俗语言解释「解决什么问题、怎么做、结果说明了什么」，避免术语堆砌（≤120字）。
- 架构·评测类（模型架构如 JEPA/世界模型/MoE/线性注意力、训练方法、智能体 harness/脚手架设计、评测基准与评测方法）：
  summary_zh 用通俗语言讲清「新在哪里、和现有做法比有什么不同、对行业意味着什么」（≤120字），并把 tech 设为 true。
- 项目类（GitHub 热门 AI 开源项目，如 Claude Skills、MCP 服务、智能体框架、本地模型工具）：
  summary_zh 讲清「它能帮你做什么、适合谁」（≤70字）；why_zh 必须是可操作的上手提示，写成「如果你在做 X，可以用它 Y」
  这样的具体用法（≤45字，例：「用 Claude Code 跑长任务时装上它，上下文不再因压缩丢掉报错信息」），不要写行业意义；
  highlights_zh 给 2–3 条上手要点（安装方式、核心用法、注意事项）。营销号、空壳、纯资源合集 quality ≤4。
tech：内容的核心是否是模型架构、训练/推理方法、智能体 harness 工程、评测方法或新基准（行业新闻、产品发布、融资为 false）。
输出严格 JSON 数组，不要任何多余文字。每个元素：
{{"id": "...", "relevant": true, "quality": 7, "title_zh": "中文标题（信达雅，≤30字）",
 "summary_zh": "一句话说清做了什么、结果如何（≤70字）", "highlights_zh": ["要点1","要点2","要点3"],
 "why_zh": "为什么值得读（≤40字）", "tags": ["大模型"], "tech": false}}"""


def _fmt(it: Item) -> str:
    srcs = "; ".join(f"{s.name} {s.signal}".strip() for s in it.sources)
    return (f"id: {it.id}\n类型: {'论文' if it.kind == 'paper' else '文章'}\n标题: {it.title}\n"
            f"分类: {it.category}\n作者/机构: {', '.join((it.orgs + it.authors)[:6])} {it.who}\n来源与热度: {srcs}\n"
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

    def daily_brief(self, items: list[dict]) -> list[dict]:
        """3–5 trend takeaways for the day, each citing item ids."""
        lines = [f"[{d['id']}] {d.get('category','')} {d.get('title_zh') or d['title']} —— {d.get('summary_zh','')[:160]}"
                 for d in items[:40]]
        system = ("你是 AI 行业主编。根据今天精选的内容，写 3–5 条「今日风向」：每条一句话判断（≤40字）+ 一句依据（≤60字），"
                  "聚焦市场方向、竞争格局、技术拐点（架构、评测方法的新变化也算），不要罗列新闻。输出严格 JSON 数组："
                  '[{"trend": "...", "why": "...", "refs": ["条目id", ...]}]')
        rows = extract_json(self.complete(system, "\n".join(lines)))
        ids = {d["id"] for d in items}
        return [{"trend": str(r.get("trend", ""))[:80], "why": str(r.get("why", ""))[:140],
                 "refs": [x for x in r.get("refs", []) if x in ids][:4]} for r in rows if isinstance(r, dict)][:5]

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
                if "tech" in r and it.category in ("资讯", "论文", "技术"):
                    it.category = "技术" if r.get("tech") is True else ("论文" if it.kind == "paper" else "资讯")
                ok += 1
        return ok

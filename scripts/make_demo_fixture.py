"""Regenerate tests/fixtures/demo_day.json: a realistic day used for E2E + design QA."""
import json
from pathlib import Path

M, N, E = "2026-09-23T00:05:00+00:00", "2026-09-23T05:05:00+00:00", "2026-09-23T11:05:00+00:00"


def src(name, signal="", metric=0, auth=.8):
    return {"name": name, "authority": auth, "url": "https://example.com", "signal": signal, "metric": metric}


def item(i, cat, lang, title, summ, slot, score, at, source, title_zh="", who="", author="", mirror=True, mt=False, links=None):
    kind = "paper" if cat == "论文" else "article"
    iid = f"arxiv-2609.2{i:04d}" if kind == "paper" else f"web-demo{i}"
    url = f"https://arxiv.org/abs/2609.2{i:04d}" if kind == "paper" else f"https://example.com/{i}"
    return {"id": iid, "kind": kind, "category": cat, "lang": lang, "who": who, "title": title, "url": url,
            "pdf_url": url.replace("abs", "pdf") if kind == "paper" else "", "abstract": "",
            "authors": [author] if author else [], "orgs": [], "published": at, "sources": [source],
            "title_zh": title_zh, "summary_zh": summ, "highlights_zh": [], "why_zh": "", "tags": [],
            "relevant": True, "score_rule": score, "score_llm": None, "score": score, "date": "2026-09-23",
            "slot": slot, "selected_at": at, "mirror_pdf": f"pdf/{iid}.pdf" if mirror else "",
            "mirror_kind": ("pdf" if kind == "paper" else "snapshot") if mirror else "",
            "links": links or ([{"label": "中文全文（幻觉翻译）", "url": f"https://hjfy.top/arxiv/2609.2{i:04d}"},
                                {"label": "arXiv", "url": url}] if kind == "paper" else [{"label": "原文", "url": url}]),
            "mt": mt}


items = [
    item(1, "观点", "en", "The Gentle Singularity, Part II",
         "Sam Altman 认为，2027 年前后 AI 将能独立完成数周长度的科研项目，真正的瓶颈会从模型能力转向电力与芯片供给。",
         "evening", .93, E, src("Sam Altman", "个人博客", 0, 1.0), title_zh="温和的奇点（二）", who="OpenAI CEO",
         author="Sam Altman", mt=True),
    item(2, "资讯", "zh", "DeepSeek 发布 V4：推理成本再降一半，开源权重同步上线",
         "DeepSeek 今天发布 V4 模型，在编程与数学基准上接近闭源旗舰，API 价格下调 50%，并同步开源权重。",
         "evening", .88, E, src("量子位", "媒体", 0, .85)),
    item(3, "观点", "zh", "为什么说 Agent 的护城河在工作流，而不在模型",
         "宝玉认为，模型能力趋同之后，谁掌握真实工作流和用户反馈数据，谁就握有 Agent 时代的护城河。",
         "noon", .86, N, src("宝玉", "个人博客", 0, .9), who="AI 布道者，前微软工程师", author="宝玉"),
    item(4, "资讯", "en", "GPT-6 Sol and Luna", "OpenAI 推出 GPT-6 Sol 和 Luna 两款模型，在能力与价格之间给出不同取舍，Luna 面向日常办公场景。",
         "noon", .84, N, src("Hacker News", "1637 pts", 1637, .55), title_zh="OpenAI 发布 GPT-6 Sol 与 Luna", mt=True),
    item(5, "资讯", "zh", "英伟达财报：数据中心收入同比增长 120%，黄仁勋称需求「看不到顶」",
         "英伟达最新季度数据中心业务收入同比增长 120%，管理层上调全年指引，并称新一代芯片订单已排到明年。",
         "morning", .82, M, src("36氪", "媒体", 0, .75)),
    item(6, "观点", "en", "Where are the AI agents?", "Ethan Mollick 认为，企业里的 AI 智能体推进缓慢，不是模型不够强，而是组织流程还没有为「数字员工」重新设计。",
         "morning", .8, M, src("Ethan Mollick", "个人博客", 0, .85), title_zh="AI 智能体都去哪了？", who="沃顿商学院教授",
         author="Ethan Mollick", mt=True),
    item(7, "资讯", "zh", "月之暗面完成新一轮融资，估值超 50 亿美元", "月之暗面完成新一轮融资，投资方包括多家国际机构，资金将用于长上下文模型与海外市场。",
         "morning", .76, M, src("机器之心", "媒体", 0, .85)),
    item(8, "资讯", "zh", "Anthropic 发布 Claude Opus 5.5：编程与长任务能力大幅提升", "Anthropic 发布 Claude Opus 5.5，在编程、智能体任务与长上下文推理上显著提升，价格不变。",
         "morning", .74, M, src("极客公园", "媒体", 0, .8)),
    item(9, "论文", "en", "RRSI: Regularized Recursive Self-Improvement of Agent Harnesses",
         "这篇论文想解决的问题是：让 AI 智能体自己改进自己时，怎么避免越改越偏。作者给「自我改写」加了约束，结果在多个任务上持续变强且更稳定——说明自我进化的智能体离实用又近了一步。",
         "evening", .79, E, src("HF Daily Papers", "▲ 174", 174, .7), title_zh="RRSI：让智能体安全地「自我进化」", mt=True),
]
Path(__file__).resolve().parent.parent.joinpath("tests/fixtures/demo_day.json").write_text(
    json.dumps({"date": "2026-09-23", "items": items}, ensure_ascii=False, indent=1), "utf-8")
print(len(items))

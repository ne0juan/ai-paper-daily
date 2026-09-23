"""Candidate sources. Each ``fetch_*`` returns a list of Items and never raises:
a broken source is logged and skipped so one outage can't sink a run."""
from __future__ import annotations

import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urljoin

from .feeds import ATOM, parse_date, parse_feed, strip_html
from .models import Item, SourceHit, arxiv_id_from, canonical_url, make_id

log = logging.getLogger(__name__)
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _safe(name):
    def deco(fn):
        def wrapper(*a, **kw):
            try:
                items = fn(*a, **kw)
                log.info("source %-18s -> %d items", name, len(items))
                return items
            except Exception as e:  # noqa: BLE001 - isolate every source
                log.warning("source %s failed: %s", name, e)
                return []
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


def _iso(d: datetime | None) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds") if d else ""


def paper_item(aid: str, title: str, abstract: str = "", authors=None, published: str = "",
               hit: SourceHit | None = None, orgs=None) -> Item:
    return Item(
        id=f"arxiv-{aid}", kind="paper", title=" ".join(title.split()),
        url=f"https://arxiv.org/abs/{aid}", pdf_url=f"https://arxiv.org/pdf/{aid}",
        abstract=" ".join((abstract or "").split()), authors=list(authors or []),
        orgs=list(orgs or []), published=published, sources=[hit] if hit else [],
        category="论文", lang="en",
    )


# --------------------------------------------------------------- 架构 · 评测 track
# Model architecture, training recipes, agent harnesses and evaluation methodology.
TECH_RE = re.compile(
    r"JEPA|world model|世界模型|architecture|架构|mixture[- ]of[- ]experts|\bMoE\b|state[- ]space|\bSSMs?\b|Mamba|"
    r"linear attention|sparse attention|线性注意力|稀疏注意力|注意力机制|attention (mechanism|variant)|diffusion (language|LLM)|"
    r"扩散语言模型|tokeni[sz]|long[- ]context|长上下文|test[- ]time (compute|scaling|training)|scaling law|缩放定律|"
    r"benchmark|基准测试|评测(方法|基准|体系|集)|模型评测|大模型评测|evaluat|\bevals?\b|leaderboard|排行榜|LMArena|"
    r"SWE-bench|ARC-AGI|Humanity'?s Last Exam|harness|scaffold|agent (framework|architecture|loop)|智能体(框架|架构)|"
    r"context engineering|上下文工程|RL environment|post-training|后训练|pre-?training|预训练|distillation|蒸馏|"
    r"positional (encoding|embedding)|RoPE|residual|normali[sz]ation|optimizer|Muon|recurren|looped|latent reasoning",
    re.I)


def is_tech(text: str) -> bool:
    return bool(TECH_RE.search(text or ""))


# --------------------------------------------------------------- HF Daily Papers
@_safe("hf_daily")
def fetch_hf_daily(http, cfg: dict, now: datetime) -> list[Item]:
    out: dict[str, Item] = {}
    for delta in (0, 1):
        day = (now - timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            data = http.json("https://huggingface.co/api/daily_papers", params={"date": day, "limit": 100})
        except Exception as e:  # noqa: BLE001
            log.info("hf_daily %s: %s", day, e)
            continue
        for row in data or []:
            p = row.get("paper") or row
            aid = arxiv_id_from(str(p.get("id", "")))
            if not aid:
                continue
            up = int(p.get("upvotes") or row.get("upvotes") or 0)
            tech = is_tech(p.get("title") or row.get("title", ""))   # title only: every abstract says "evaluate"
            if up < cfg.get("tech_min_upvotes" if tech else "min_upvotes", cfg.get("min_upvotes", 0)):
                continue
            org = (row.get("organization") or p.get("organization") or {})
            orgs = [org.get("fullname") or org.get("name")] if isinstance(org, dict) and org else []
            hit = SourceHit("HF Daily Papers", cfg.get("authority", 0.8),
                            f"https://huggingface.co/papers/{aid}", f"▲ {up}", up)
            it = paper_item(aid, p.get("title") or row.get("title", ""), p.get("summary", ""),
                            [a.get("name", "") for a in p.get("authors", []) if isinstance(a, dict)],
                            p.get("publishedAt", ""), hit, [o for o in orgs if o])
            if tech:
                it.category = "技术"
            if p.get("githubRepo"):
                it.extra["github"] = p["githubRepo"]
            if p.get("ai_keywords"):
                it.extra["keywords"] = p["ai_keywords"][:8]
            if it.id in out:
                out[it.id].merge(it)
            else:
                out[it.id] = it
    return list(out.values())


# --------------------------------------------------------------- Hacker News
@_safe("hackernews")
def fetch_hackernews(http, cfg: dict, now: datetime) -> list[Item]:
    since = int((now - timedelta(hours=cfg.get("lookback_hours", 36))).timestamp())
    data = http.json("https://hn.algolia.com/api/v1/search", params={
        "tags": "story", "hitsPerPage": 300,
        "numericFilters": f"created_at_i>{since},points>={cfg.get('min_points', 60)}",
    })
    kws = [k.lower() for k in cfg.get("keywords", [])]
    domains = cfg.get("domains", [])
    items = []
    for h in data.get("hits", []):
        url, title = h.get("url") or "", h.get("title") or ""
        if not url or not title:
            continue
        hay = f" {title.lower()} "
        if not (any(d in url for d in domains) or any(k in hay for k in kws)):
            continue
        pts = int(h.get("points") or 0)
        hit = SourceHit("Hacker News", cfg.get("authority", 0.55),
                        f"https://news.ycombinator.com/item?id={h.get('objectID')}", f"{pts} pts", pts)
        aid = arxiv_id_from(url)
        if aid:
            it = paper_item(aid, title, hit=hit, published=h.get("created_at", ""))
            it.extra["needs_arxiv_meta"] = True
        else:
            if url.lower().split("?")[0].endswith(".pdf"):
                it = Item(make_id(url), "paper", title, url, pdf_url=url, sources=[hit], category="论文",
                          published=h.get("created_at", ""))
            else:
                it = Item(make_id(url), "article", title, url, sources=[hit], published=h.get("created_at", ""))
        items.append(it)
    return items


# --------------------------------------------------------------- RSS feeds
AI_WORDS = re.compile(
    r"\bAI\b|AGI|LLM|GPT|Claude|Gemini|Llama|DeepSeek|OpenAI|Anthropic|DeepMind|Grok|Copilot|Agent|智能体|"
    r"人工智能|大模型|模型|生成式|算力|英伟达|NVIDIA|机器人|具身|推理|Kimi|通义|千问|豆包|文心|智谱|月之暗面|MiniMax|Sora|"
    r"agent|chatbot|machine learning|neural|transformer|reasoning model", re.I)
KIND_ZH = {"opinion": "观点", "news": "资讯", "official": "资讯", "paper": "论文", "tech": "技术"}


def is_ai(text: str) -> bool:
    return bool(AI_WORDS.search(text or ""))


@_safe("rss")
def fetch_rss(http, feeds: list[dict], now: datetime, lookback_hours: int = 72) -> list[Item]:
    items = []
    cutoff = now - timedelta(hours=lookback_hours)
    for f in feeds:
        try:
            entries = parse_feed(http.text(f["url"]))
        except Exception as e:  # noqa: BLE001
            log.warning("rss %s failed: %s", f["name"], e)
            continue
        n = 0
        kind = f.get("kind", "news")
        for e in entries[: f.get("max_entries", 40)]:
            pub = e["published"]
            if pub and pub < cutoff:
                continue
            if not pub:   # freshness first: an undated entry can't prove it is new
                continue
            if f.get("filter") == "ai" and not is_ai(e["title"] + " " + e["summary"][:400]):
                continue
            link = e["link"]
            signal = {"opinion": "个人博客", "official": "官方发布", "news": "媒体", "tech": "技术博客"}.get(kind, "")
            hit = SourceHit(f["name"], f.get("authority", 0.7), link, signal)
            aid = arxiv_id_from(link)
            if aid:
                it = paper_item(aid, e["title"], e["summary"], e["authors"], _iso(pub), hit)
            else:
                it = Item(make_id(link), "article", e["title"], link, abstract=e["summary"][:1500],
                          authors=e["authors"] or ([f["who_name"]] if f.get("who_name") else []),
                          orgs=[f["name"]], published=_iso(pub), sources=[hit])
                it.category = KIND_ZH.get(kind, "资讯")
                it.lang = f.get("lang", "en")
                it.who = f.get("who", "")
            items.append(it)
            n += 1
    return items


# --------------------------------------------------------------- HTML list pages
HREF_RE = re.compile(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.I | re.S)
META_RE = re.compile(r"<meta\s+[^>]*(?:property|name)=\"([^\"]+)\"[^>]*content=\"([^\"]*)\"", re.I)
META_RE2 = re.compile(r"<meta\s+[^>]*content=\"([^\"]*)\"[^>]*(?:property|name)=\"([^\"]+)\"", re.I)


def page_meta(html_text: str) -> dict:
    meta = {k.lower(): unescape(v) for k, v in META_RE.findall(html_text)}
    meta.update({k.lower(): unescape(v) for v, k in META_RE2.findall(html_text) if k.lower() not in meta})
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    if m:
        meta.setdefault("title", strip_html(m.group(1)))
    return meta


@_safe("html_lists")
def fetch_html_lists(http, lists: list[dict], state: dict, now: datetime, max_new: int = 4) -> list[Item]:
    """Discover new article links on list pages. ``state['known_links']`` remembers
    links already seen so only genuinely new posts become candidates."""
    known: dict = state.setdefault("known_links", {})
    items, seen_now = [], set()
    for L in lists:
        try:
            page = http.text(L["url"])
        except Exception as e:  # noqa: BLE001
            log.warning("list %s failed: %s", L["name"], e)
            continue
        pat = re.compile(L["pattern"])
        first_time = L["url"] not in known
        links = []
        for href, _txt in HREF_RE.findall(page):
            path = href.replace(L["base"], "") if href.startswith("http") else href
            path = path.split("?")[0].split("#")[0]
            if pat.match(path) and path not in links:
                links.append(path)
        prev = set(known.get(L["url"], []))
        new = [p for p in links if p not in prev and p not in seen_now]
        known[L["url"]] = sorted(set(links) | prev)[-500:]
        if first_time:
            new = new[:2]   # bootstrap: only the top of the page, don't flood with history
        for path in new[:max_new]:
            seen_now.add(path)
            url = urljoin(L["base"], path)
            try:
                meta = page_meta(http.text(url))
            except Exception as e:  # noqa: BLE001
                log.info("meta %s: %s", url, e)
                continue
            title = meta.get("og:title") or meta.get("title") or path.rsplit("/", 1)[-1]
            pub = parse_date(meta.get("article:published_time") or meta.get("date"))
            if pub and pub < now - timedelta(days=7):
                continue
            if first_time and not pub:   # bootstrap: an undated old post must not pass as news
                continue
            hit = SourceHit(L["name"], L.get("authority", 0.8), url, "个人博客" if L.get("who") else "官方")
            it = Item(make_id(url), "article", title.split(" \\ ")[0].strip(), url,
                      abstract=meta.get("og:description") or meta.get("description", ""),
                      authors=[L["who_name"]] if L.get("who_name") else [],
                      orgs=[L["name"]], published=_iso(pub), sources=[hit],
                      category=L.get("category", "观点" if L.get("who") else "资讯"), lang=L.get("lang", "en"))
            it.who = L.get("who", "")
            items.append(it)
    return items


# --------------------------------------------------------------- X / Twitter
@_safe("x")
def fetch_x(http, cfg: dict, now: datetime) -> list[Item]:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return []
    H = {"Authorization": f"Bearer {token}"}
    users = http.json("https://api.x.com/2/users/by", headers=H,
                      params={"usernames": ",".join(cfg.get("accounts", [])[:100])}).get("data", [])
    since = (now - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []
    for u in users:
        tw = http.json(f"https://api.x.com/2/users/{u['id']}/tweets", headers=H, params={
            "max_results": 20, "exclude": "replies", "start_time": since,
            "tweet.fields": "public_metrics,entities,created_at"}).get("data", [])
        for t in tw:
            likes = t.get("public_metrics", {}).get("like_count", 0)
            if likes < cfg.get("min_likes", 200):
                continue
            for link in t.get("entities", {}).get("urls", []):
                url = link.get("unwound_url") or link.get("expanded_url") or ""
                if not url or re.search(r"//(x|twitter)\.com/", url):
                    continue
                hit = SourceHit(f"X @{u['username']}", cfg.get("authority", 0.75),
                                f"https://x.com/{u['username']}/status/{t['id']}", f"♥ {likes}", likes)
                aid = arxiv_id_from(url)
                if aid:
                    it = paper_item(aid, link.get("title") or aid, hit=hit)
                    it.extra["needs_arxiv_meta"] = True
                else:
                    it = Item(make_id(url), "article", link.get("title") or t.get("text", "")[:120], url,
                              abstract=link.get("description", "") or t.get("text", ""), sources=[hit],
                              published=t.get("created_at", ""))
                items.append(it)
    return items


# --------------------------------------------------------------- arXiv metadata
def parse_arxiv_atom(xml_text: str) -> dict[str, dict]:
    root = ET.fromstring(xml_text.encode())
    out = {}
    for e in root.findall(f"{ATOM}entry"):
        aid = arxiv_id_from(e.findtext(f"{ATOM}id", ""))
        if not aid:
            continue
        authors, affs = [], []
        for a in e.findall(f"{ATOM}author"):
            authors.append((a.findtext(f"{ATOM}name") or "").strip())
            for af in a.findall(f"{ARXIV_NS}affiliation"):
                if af.text and af.text.strip() not in affs:
                    affs.append(af.text.strip())
        out[aid] = {
            "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
            "abstract": " ".join((e.findtext(f"{ATOM}summary") or "").split()),
            "authors": authors, "orgs": affs,
            "published": e.findtext(f"{ATOM}published", ""),
            "categories": [c.get("term") for c in e.findall(f"{ATOM}category")],
            "comment": (e.findtext(f"{ARXIV_NS}comment") or "").strip(),
        }
    return out


def enrich_arxiv(http, items: list[Item]) -> None:
    """Fill title/abstract/authors for papers discovered via HN/X/RSS links."""
    need = [it for it in items if it.arxiv_id and (it.extra.get("needs_arxiv_meta") or not it.abstract)]
    for i in range(0, len(need), 40):
        chunk = need[i:i + 40]
        try:
            meta = parse_arxiv_atom(http.text("https://export.arxiv.org/api/query", params={
                "id_list": ",".join(it.arxiv_id for it in chunk), "max_results": len(chunk)}))
        except Exception as e:  # noqa: BLE001
            log.warning("arxiv enrich failed: %s", e)
            continue
        for it in chunk:
            m = meta.get(it.arxiv_id)
            if not m:
                continue
            it.title = m["title"] or it.title
            it.abstract = m["abstract"] or it.abstract
            it.authors = it.authors or m["authors"]
            it.orgs = it.orgs or m["orgs"]
            it.published = it.published or m["published"]
            it.extra["categories"] = m["categories"]
            if m["comment"]:
                it.extra["comment"] = m["comment"]
            it.extra.pop("needs_arxiv_meta", None)


@_safe("arxiv_search")
def fetch_arxiv_search(http, cfg: dict, now: datetime) -> list[Item]:
    """Fresh arXiv papers on architecture / evaluation topics (e.g. JEPA, world models,
    harnesses, new benchmarks). No popularity signal — the LLM judge decides."""
    terms = cfg.get("terms", [])
    if not terms:
        return []
    q = " OR ".join(f'ti:"{t}"' if " " in t else f"ti:{t}" for t in terms)
    cats = cfg.get("categories", ["cs.CL", "cs.LG", "cs.AI"])
    query = f"({q}) AND ({' OR '.join('cat:' + c for c in cats)})"
    meta = parse_arxiv_atom(http.text("https://export.arxiv.org/api/query", params={
        "search_query": query, "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": cfg.get("max_results", 40)}))
    cutoff = now - timedelta(hours=cfg.get("lookback_hours", 48))
    items = []
    for aid, m in meta.items():
        pub = parse_date(m["published"])
        if not pub or pub < cutoff:
            continue
        hit = SourceHit("arXiv 新论文", cfg.get("authority", 0.55), f"https://arxiv.org/abs/{aid}", "arXiv")
        it = paper_item(aid, m["title"], m["abstract"], m["authors"], m["published"], hit, m["orgs"])
        it.category = "技术"
        it.extra["categories"] = m["categories"]
        items.append(it)
    return items


# --------------------------------------------------------------- GitHub: hot AI projects (应用侧)
GH_AI_RE = re.compile(
    r"\bAI\b|LLM|GPT|Claude|Gemini|Llama|Qwen|DeepSeek|agent|MCP|\bskills?\b|prompt|RAG\b|embedding|"
    r"chatbot|copilot|cursor|codex|openai|anthropic|ollama|vllm|diffusion|comfyui|stable diffusion|"
    r"transformer|inference|fine-?tun|LoRA|machine learning|deep learning|neural|speech|TTS|ASR|"
    r"multimodal|vision-language|text-to|智能体|大模型|人工智能", re.I)
TRENDING_ROW = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)


def _gh_item(repo: str, desc: str, lang: str, stars: int, signal: str, metric: int, source: str,
             authority: float, now: datetime) -> Item:
    url = f"https://github.com/{repo}"
    hit = SourceHit(source, authority, url, f"{signal} · {lang}" if lang else signal, metric)
    it = Item(make_id(url), "article", repo, url, abstract=desc, orgs=[repo.split("/")[0]],
              published=_iso(now), sources=[hit], category="项目", lang="en")
    it.extra.update({"repo": repo, "stars": stars, "language": lang})
    return it


@_safe("github")
def fetch_github(http, cfg: dict, now: datetime) -> list[Item]:
    """Two signals: (1) GitHub Trending (stars gained today) for AI-related repos,
    (2) brand-new repos (created in the last few days) that are already taking off.
    Top candidates get a README excerpt so the LLM can write practical usage tips."""
    items: dict[str, Item] = {}
    for path in cfg.get("trending_paths", ["", "python", "typescript"]):
        try:
            page = http.text(f"https://github.com/trending/{path}".rstrip("/"), params={"since": "daily"})
        except Exception as e:  # noqa: BLE001
            log.info("trending %s: %s", path, e)
            continue
        for row in TRENDING_ROW.findall(page):
            m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', row)
            if not m:
                continue
            repo = m.group(1)
            desc = strip_html((re.search(r"<p[^>]*>(.*?)</p>", row, re.S) or [None, ""])[1])
            lang = strip_html((re.search(r'itemprop="programmingLanguage">(.*?)<', row) or [None, ""])[1])
            total = re.search(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', row, re.S)
            today = re.search(r"([\d,]+)\s+stars (today|this week)", row)
            if not today or not GH_AI_RE.search(f"{repo} {desc}"):
                continue
            n_today = int(today.group(1).replace(",", ""))
            if n_today < cfg.get("min_stars_today", 100):
                continue
            n_total = int(re.sub(r"\D", "", strip_html(total.group(1))) or 0) if total else 0
            it = _gh_item(repo, desc, lang, n_total, f"★ 今日 +{n_today:,}", n_today, "GitHub Trending",
                          cfg.get("authority", 0.6), now)
            if it.id not in items:
                items[it.id] = it
    # brand-new repos gaining traction fast (search API; GITHUB_TOKEN raises the rate limit when present)
    since = (now - timedelta(days=cfg.get("new_repo_days", 7))).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        data = http.json("https://api.github.com/search/repositories", headers=headers, params={
            "q": f"created:>{since} stars:>={cfg.get('new_repo_min_stars', 300)}", "sort": "stars", "per_page": 50})
        for r in data.get("items", []):
            text = f"{r.get('full_name', '')} {r.get('description') or ''} {' '.join(r.get('topics') or [])}"
            if not GH_AI_RE.search(text):
                continue
            it = _gh_item(r["full_name"], r.get("description") or "", r.get("language") or "",
                          r.get("stargazers_count", 0), f"★ {r.get('stargazers_count', 0):,}（新项目）",
                          r.get("stargazers_count", 0), "GitHub 新项目", cfg.get("authority", 0.6), now)
            it.extra["topics"] = (r.get("topics") or [])[:8]
            if it.id not in items:
                items[it.id] = it
    except Exception as e:  # noqa: BLE001
        log.info("github search: %s", e)
    scale = {"GitHub Trending": 1500, "GitHub 新项目": 5000}   # same scales as scoring.POP_SCALE
    ranked = sorted(items.values(), key=lambda i: popularity(i.sources[0].metric, scale[i.sources[0].name]),
                    reverse=True)[: cfg.get("max_items", 20)]
    for it in ranked[: cfg.get("readme_for_top", 12)]:
        try:
            readme = http.text(f"https://raw.githubusercontent.com/{it.extra['repo']}/HEAD/README.md")
            it.abstract = (it.abstract + "\nREADME: " + strip_html(readme)[:1500]).strip()
        except Exception:  # noqa: BLE001
            pass
    return ranked


def collect(http, cfg: dict, state: dict, now: datetime) -> list[Item]:
    """Run all sources and merge duplicates (same arXiv id / canonical URL)."""
    raw: list[Item] = []
    if cfg.get("hf_daily", {}).get("enabled", True):
        raw += fetch_hf_daily(http, cfg.get("hf_daily", {}), now)
    if cfg.get("hackernews", {}).get("enabled", True):
        raw += fetch_hackernews(http, cfg.get("hackernews", {}), now)
    raw += fetch_rss(http, cfg.get("rss", []), now, cfg.get("rss_lookback_hours", 72))
    raw += fetch_html_lists(http, cfg.get("html_lists", []), state, now)
    raw += fetch_x(http, cfg.get("x", {}), now)
    if cfg.get("arxiv_search", {}).get("enabled", False):
        raw += fetch_arxiv_search(http, cfg["arxiv_search"], now)
    if cfg.get("github", {}).get("enabled", False):
        raw += fetch_github(http, cfg["github"], now)
    merged: dict[str, Item] = {}
    for it in raw:
        if it.id in merged:
            merged[it.id].merge(it)
        else:
            merged[it.id] = it
    items = list(merged.values())
    enrich_arxiv(http, items)
    for it in items:   # hot papers about architecture / evaluation join the 架构·评测 track
        if it.category == "论文" and is_tech(it.title):
            it.category = "技术"
    return items


def is_fresh(it: Item, now: datetime, max_age_days: int = 1) -> bool:
    """Freshness rule: published today or yesterday (Beijing calendar, i.e. T or T-1).
    Items without a date are kept only when they come from a signal that is itself
    recent (HF daily list of today/yesterday, HN front page, newly discovered links)."""
    pub = parse_date(it.published)
    if pub is None:
        return any(s.name in ("HF Daily Papers", "Hacker News") or s.signal in ("官方", "个人博客") for s in it.sources)
    bj = timezone(timedelta(hours=8))
    start = (now.astimezone(bj) - timedelta(days=max_age_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    if pub >= start:
        return True
    # HF daily features papers a few days after submission; being featured today/yesterday is the news
    return any(s.name == "HF Daily Papers" for s in it.sources)


def popularity(metric: float, scale: float) -> float:
    """Map raw upvotes/points to 0..1 with diminishing returns."""
    return min(1.0, math.log1p(max(metric, 0)) / math.log1p(scale))

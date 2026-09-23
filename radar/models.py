"""Core data model shared by sources, scoring, LLM enrichment and the site."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv:|^)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def arxiv_id_from(text: str | None) -> str | None:
    """Extract a modern arXiv id (e.g. 2409.12345) from a URL or string."""
    if not text:
        return None
    m = ARXIV_ID_RE.search(text.strip())
    return m.group(1) if m else None


def canonical_url(url: str) -> str:
    """Normalise a URL for dedup: https, no fragment/query tracking, no trailing slash."""
    parts = urlsplit(url.strip())
    query = "&".join(
        q for q in parts.query.split("&") if q and not q.lower().startswith(("utm_", "ref=", "source="))
    )
    path = parts.path.rstrip("/") or "/"
    netloc = parts.netloc.lower().removeprefix("www.")
    return urlunsplit(("https", netloc, path, query, ""))


def make_id(url: str) -> str:
    aid = arxiv_id_from(url)
    if aid:
        return f"arxiv-{aid}"
    return "web-" + hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12]


@dataclass
class SourceHit:
    name: str                 # e.g. "HF Daily Papers", "Hacker News", "OpenAI"
    authority: float          # 0..1
    url: str = ""             # discussion / source page
    signal: str = ""          # human readable, e.g. "▲ 132"
    metric: float = 0.0       # numeric popularity signal (upvotes / points / likes)


@dataclass
class Item:
    id: str
    kind: str                 # "paper" | "article"
    title: str
    url: str                  # landing page (abs page / blog post)
    pdf_url: str = ""         # origin PDF if any
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)
    published: str = ""       # ISO date-time (UTC) if known
    sources: list[SourceHit] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # enrichment
    title_zh: str = ""
    summary_zh: str = ""
    highlights_zh: list[str] = field(default_factory=list)
    why_zh: str = ""
    tags: list[str] = field(default_factory=list)
    relevant: bool = True
    score_rule: float = 0.0
    score_llm: float | None = None
    score: float = 0.0

    # publication
    date: str = ""            # Beijing date it was selected (YYYY-MM-DD)
    slot: str = ""            # morning | noon | evening
    selected_at: str = ""
    mirror_pdf: str = ""      # site-relative path to our mirrored PDF
    mirror_kind: str = ""     # "pdf" (original) | "snapshot" (rendered page)
    links: list[dict[str, str]] = field(default_factory=list)

    @property
    def arxiv_id(self) -> str | None:
        return arxiv_id_from(self.id.removeprefix("arxiv-")) if self.id.startswith("arxiv-") else None

    def merge(self, other: "Item") -> None:
        """Merge another hit of the same item (seen from another source)."""
        known = {(s.name, s.url) for s in self.sources}
        for s in other.sources:
            if (s.name, s.url) not in known:
                self.sources.append(s)
        for attr in ("abstract", "pdf_url", "published"):
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))
        if len(other.abstract) > len(self.abstract):
            self.abstract = other.abstract
        if not self.authors and other.authors:
            self.authors = other.authors
        for o in other.orgs:
            if o not in self.orgs:
                self.orgs.append(o)
        # prefer arxiv-style title from paper sources over HN headline
        if other.kind == "paper" and self.kind != "paper":
            self.kind, self.title, self.url = "paper", other.title, other.url
        self.extra.update({k: v for k, v in other.extra.items() if k not in self.extra})

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("extra", None)
        d["abstract"] = self.abstract[:2000]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Item":
        d = dict(d)
        d["sources"] = [SourceHit(**s) for s in d.get("sources", [])]
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})

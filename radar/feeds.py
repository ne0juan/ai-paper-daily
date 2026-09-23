"""Minimal RSS 2.0 / Atom parser (stdlib only) + date helpers."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", s))).strip()


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
            if not m:
                return None
            d = datetime.fromisoformat(m.group(1))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _text(el, *names: str) -> str:
    for n in names:
        found = el.find(n)
        if found is not None:
            if found.text and found.text.strip():
                return found.text.strip()
            if found.get("href"):
                return found.get("href")
    return ""


def parse_feed(xml_text: str) -> list[dict]:
    """Return [{title, link, summary, published(datetime|None), authors}]."""
    xml_text = xml_text.lstrip("\ufeff \n\r\t")
    root = ET.fromstring(xml_text.encode("utf-8"))
    entries: list[dict] = []
    if root.tag == f"{ATOM}feed":
        for e in root.findall(f"{ATOM}entry"):
            link = ""
            for l in e.findall(f"{ATOM}link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            entries.append({
                "title": strip_html(_text(e, f"{ATOM}title")).strip("*# "),
                "link": link,
                "summary": strip_html(_text(e, f"{ATOM}summary", f"{ATOM}content")),
                "published": parse_date(_text(e, f"{ATOM}published", f"{ATOM}updated")),
                "authors": [strip_html(_text(a, f"{ATOM}name")) for a in e.findall(f"{ATOM}author")],
            })
    else:
        channel = root.find("channel")
        items = (channel if channel is not None else root).iter("item")
        for e in items:
            entries.append({
                "title": strip_html(_text(e, "title")).strip("*# "),
                "link": _text(e, "link", "guid"),
                "summary": strip_html(_text(e, "description", f"{CONTENT}encoded")),
                "published": parse_date(_text(e, "pubDate", f"{DC}date")),
                "authors": [a for a in [strip_html(_text(e, f"{DC}creator", "author"))] if a],
            })
    return [x for x in entries if x["title"] and x["link"]]

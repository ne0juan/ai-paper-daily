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
INVISIBLE_RE = re.compile("[\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u2064\\ufeff\\u00ad]")  # zero-width anti-scrape marks


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    return WS_RE.sub(" ", INVISIBLE_RE.sub("", html.unescape(TAG_RE.sub(" ", s)))).strip()


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


ITEM_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.S | re.I)


def _field(block: str, *names: str) -> str:
    for n in names:
        m = re.search(rf"<{n}\b([^>]*)>(.*?)</{n}>", block, re.S | re.I)
        if m and m.group(2).strip():
            v = m.group(2).strip()
            c = re.match(r"<!\[CDATA\[(.*)\]\]>$", v, re.S)
            return c.group(1) if c else v
        m = re.search(rf"<{n}\b[^>]*href=[\"']([^\"']+)", block, re.I)
        if m:
            return m.group(1)
    return ""


def parse_feed_lenient(xml_text: str) -> list[dict]:
    """Regex fallback for feeds that are not well-formed XML (unescaped &, stray bytes...)."""
    out = []
    for _, b in ITEM_RE.findall(xml_text):
        out.append({"title": strip_html(_field(b, "title")),
                    "link": html.unescape(_field(b, "link", "guid", "id").strip()),
                    "summary": strip_html(_field(b, "description", "summary", "content:encoded", "content")),
                    "published": parse_date(_field(b, "pubDate", "published", "updated", "dc:date")),
                    "authors": [a for a in [strip_html(_field(b, "dc:creator", "author", "name"))] if a]})
    return [x for x in out if x["title"] and x["link"].startswith("http")]


def parse_feed(xml_text: str) -> list[dict]:
    """Return [{title, link, summary, published(datetime|None), authors}]."""
    xml_text = xml_text.lstrip("\ufeff \n\r\t")
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return parse_feed_lenient(xml_text)
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

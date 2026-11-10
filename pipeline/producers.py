#!/usr/bin/env python3
"""Producers: collect CEO headlines from multiple sources -> headlines.raw.

Kafka does not collect anything; these are the hands. One producer per
source, each independently runnable:

  python3 -m pipeline.producers google-news   (Google News RSS per CEO)
  python3 -m pipeline.producers gdelt         (GDELT 2.0, global, no key)
  python3 -m pipeline.producers outlet-rss    (BBC / CNBC RSS, filtered)
  python3 -m pipeline.producers all

Every record is keyed by CEO name, so all of one CEO's headlines land in the
same partition and stay ordered. Every producer degrades gracefully: a dead
source logs and moves on, it never kills the run.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.common import TOPIC_RAW, load_core, stage_banner, transport_from_args

CORE = load_core()
REL_WORDS = re.compile(r"affair|dating|relationship|divorce|wife|husband|girlfriend|"
                       r"boyfriend|kiss|scandal|lover|mistress|romanc", re.I)


def emit(transport, ceo, title, link, date, source, via):
    transport.produce(TOPIC_RAW, ceo["name"], {
        "ceo": ceo["name"], "company": ceo["company"], "status": ceo["status"],
        "title": title, "link": link, "date": date, "source": source,
        "via": via, "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    return 1


def produce_google_news(transport):
    total = 0
    for ceo in CORE.CEOS:
        heads = CORE.fetch_headlines(ceo["queries"])
        for h in heads:
            total += emit(transport, ceo, h["title"], h["link"], h["date"],
                          h["source"], "google-news")
        print(f"[google-news] {ceo['name']}: {len(heads)}")
    return total


def produce_gdelt(transport):
    """GDELT Doc 2.0. Throttles hard (shared IPs see 429s), so: one query per
    CEO, short timeouts, one retry — and a circuit breaker: after 3 CEOs in a
    row fail completely, this IP is throttled and we stop asking."""
    total = 0
    consecutive_failures = 0
    for i, ceo in enumerate(CORE.CEOS):
        if consecutive_failures >= 3:
            print("[gdelt] circuit breaker: throttled IP suspected, skipping remaining CEOs")
            break
        if i:
            time.sleep(5)
        q = f'"{ceo["name"]}" affair OR dating OR divorce OR relationship'
        url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
               + urllib.parse.quote(q)
               + "&mode=ArtList&maxrecords=10&timespan=3m&format=json")
        arts = []
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                arts = json.load(urllib.request.urlopen(req, timeout=12)).get("articles", [])
                break
            except Exception as exc:
                print(f"[gdelt] {ceo['name']}: attempt {attempt + 1} failed ({type(exc).__name__})")
                time.sleep(4)
        consecutive_failures = consecutive_failures + 1 if not arts else 0
        for a in arts:
            title = (a.get("title") or "").strip()
            if title:
                total += emit(transport, ceo, title, a.get("url", ""),
                              a.get("seendate", ""), a.get("domain", "gdelt"), "gdelt")
        print(f"[gdelt] {ceo['name']}: {len(arts)}")
    return total


OUTLET_FEEDS = [
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?id=100003114"),
]


def produce_outlet_rss(transport):
    total = 0
    for outlet, url in OUTLET_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(urllib.request.urlopen(req, timeout=25).read())
        except Exception as exc:
            print(f"[outlet-rss] {outlet}: fetch failed ({type(exc).__name__})")
            continue
        items = list(root.iter("item"))
        kept = 0
        for item in items:
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "")
            for ceo in CORE.CEOS:
                surname = ceo["name"].split()[-1]
                if surname.lower() in (title + " " + desc).lower() and REL_WORDS.search(title + " " + desc):
                    kept += emit(transport, ceo, title, item.findtext("link") or "",
                                 item.findtext("pubDate") or "", outlet, "outlet-rss")
                    break
        print(f"[outlet-rss] {outlet}: {len(items)} items, {kept} matched")
        total += kept
    return total


SCOUT_PROMPT = """Search the web for RECENT news headlines (prefer the last 90 days) about
{name}, CEO of {company}, concerning romantic relationships, affairs, dating,
divorce, workplace relationships, or related scandals and investigations.

Return ONLY a JSON array of up to 5 items, no markdown, each:
{{"title": "<headline as published>", "url": "<article URL>",
  "source": "<outlet name>", "date": "<YYYY-MM-DD or best estimate>"}}
Use only real headlines you actually found in search results. If you find
nothing relevant, return []."""


def produce_ai_scout(transport):
    """AI scout producer: an OpenAI model WITH web search goes and finds
    headlines itself, complementing the dumb RSS/API producers. Its finds
    flow through the same dedupe (where overlap with RSS counts as reach)
    and the same scoring rubric — the scout proposes, the pipeline disposes.
    No API key -> quietly emits nothing; any failure is non-fatal."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("[ai-scout] no OPENAI_API_KEY — skipping")
        return 0
    total = 0
    for ceo in CORE.CEOS:
        try:
            body = json.dumps({
                "model": os.environ.get("HORNINESS_SCOUT_MODEL") or "gpt-4o-mini",
                "tools": [{"type": "web_search"}],
                "input": SCOUT_PROMPT.format(name=ceo["name"], company=ceo["company"]),
            }).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/responses", data=body,
                headers={"content-type": "application/json",
                         "authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.load(resp)
            text = payload.get("output_text") or ""
            if not text:
                for item in payload.get("output", []):
                    for c in item.get("content", []):
                        if c.get("type") == "output_text":
                            text += c.get("text", "")
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
            found = json.loads(text)
            kept = 0
            for h in found[:5]:
                title = str(h.get("title", "")).strip()
                if not title:
                    continue
                kept += emit(transport, ceo, title, str(h.get("url", "")),
                             str(h.get("date", "")), str(h.get("source", "web")),
                             "ai-scout")
            total += kept
            print(f"[ai-scout] {ceo['name']}: {kept}")
        except Exception as exc:
            print(f"[ai-scout] {ceo['name']}: failed ({type(exc).__name__}) — non-fatal")
    return total


PRODUCERS = {"google-news": produce_google_news, "gdelt": produce_gdelt,
             "outlet-rss": produce_outlet_rss, "ai-scout": produce_ai_scout}


def run(transport, which=("all",)):
    stage_banner("producers")
    names = list(PRODUCERS) if which[0] == "all" else which
    total = 0
    for name in names:
        total += PRODUCERS[name](transport)
    transport.flush()
    print(f"producers: {total} raw headlines -> {TOPIC_RAW}")
    return total


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    t = transport_from_args(sys.argv[1:])
    try:
        run(t, args or ("all",))
    finally:
        t.close()

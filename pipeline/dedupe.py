#!/usr/bin/env python3
"""Dedupe consumer: headlines.raw -> headlines.clean.

The same story syndicated to thirty outlets is ONE story. This stage
collapses copies (exact-normalised titles, then a token-overlap fuzzy pass)
into a single clean record carrying:
  reach        — how many placements the story got (distinct outlets)
  also_in      — the outlets it appeared in
  via_sources  — which producers found it
Reach feeds a small, capped scoring boost in the scorer: syndication is a
signal, it just isn't thirty signals.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.common import (TOPIC_CLEAN, TOPIC_RAW, load_core,
                                 stage_banner, transport_from_args)

CORE = load_core()

STOP = set("the a an of to in on for and or as at by with s".split())


def story_key(title):
    t = re.sub(r"\s+-\s+[^-]+$", "", title)          # strip " - Outlet" suffix
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return " ".join(w for w in t.split() if w not in STOP)


def tokens(key):
    return set(key.split())


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe(records):
    clusters = {}  # ceo -> list of cluster dicts
    order = []
    for rec in records:
        ceo = rec["ceo"]
        key = story_key(rec["title"])
        toks = tokens(key)
        bucket = clusters.setdefault(ceo, [])
        hit = None
        for cl in bucket:
            if cl["key"] == key or jaccard(cl["tokens"], toks) >= 0.8:
                hit = cl
                break
        if hit is None:
            hit = {"key": key, "tokens": toks, "rep": rec, "outlets": set(),
                   "vias": set(), "placements": 0}
            bucket.append(hit)
            order.append((ceo, hit))
        hit["placements"] += 1
        if rec.get("image") and not hit.get("image"):
            hit["image"] = rec["image"]
        # The story's date is its NEWEST placement's date: a 2019 scandal
        # syndicated again today is news today.
        cur = CORE.parse_date(hit.get("newest_date"))
        newd = CORE.parse_date(rec.get("date"))
        if newd is not None and (cur is None or newd > cur):
            hit["newest_date"] = rec.get("date")
        if rec.get("source"):
            hit["outlets"].add(rec["source"])
        if rec.get("via"):
            hit["vias"].add(rec["via"])
        # Representative = the longest (usually most complete) title.
        if len(rec["title"]) > len(hit["rep"]["title"]):
            hit["rep"] = rec
    out = []
    for ceo, cl in order:
        rep = cl["rep"]
        out.append({
            "ceo": ceo, "company": rep["company"], "status": rep["status"],
            "title": rep["title"], "link": rep["link"],
            "image": cl.get("image", ""),
            "date": cl.get("newest_date") or rep["date"],
            "source": rep["source"],
            "reach": max(cl["placements"], len(cl["outlets"])),
            "placements": cl["placements"],
            "also_in": sorted(cl["outlets"]),
            "via_sources": sorted(cl["vias"]),
        })
    return out


def run(transport):
    stage_banner("dedupe")
    raw = transport.consume_all(TOPIC_RAW, group="horniness-dedupe")
    clean = dedupe(raw)
    by_ceo = {}
    for rec in clean:
        transport.produce(TOPIC_CLEAN, rec["ceo"], rec)
        by_ceo[rec["ceo"]] = by_ceo.get(rec["ceo"], 0) + 1
    transport.flush()
    print(f"dedupe: {len(raw)} raw -> {len(clean)} clean stories "
          f"({len(raw) - len(clean)} syndicated duplicates collapsed)")
    for ceo, n in sorted(by_ceo.items()):
        print(f"  {ceo}: {n} stories")
    return raw, clean


if __name__ == "__main__":
    t = transport_from_args(sys.argv[1:])
    try:
        run(t)
    finally:
        t.close()

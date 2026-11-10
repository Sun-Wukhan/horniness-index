#!/usr/bin/env python3
"""Aggregator / sink: headlines.clean + ceo.scores -> the Pages site.

This is the aggregation step the whole pipeline exists for: it joins the
scored CEOs with their deduplicated stories and renders the leaderboard
(index.html + data.json) using the core renderer, so the Kafka edition and
the standalone edition are the same page with the same rubric.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.common import (TOPIC_CLEAN, TOPIC_SCORES, load_core,
                                 stage_banner, transport_from_args)

CORE = load_core()


def run(transport, out_dir):
    stage_banner("aggregator")
    clean = transport.consume_all(TOPIC_CLEAN, group="horniness-aggregator")
    scores = transport.consume_all(TOPIC_SCORES, group="horniness-aggregator")
    by_ceo = defaultdict(list)
    for rec in clean:
        by_ceo[rec["ceo"]].append(rec)
    score_by_ceo = {s["ceo"]: s for s in scores}
    results = []
    for ceo in CORE.CEOS:
        s = score_by_ceo.get(ceo["name"])
        if not s:
            continue
        stories = sorted(by_ceo.get(ceo["name"], []), key=lambda r: -r["reach"])
        results.append({
            "name": ceo["name"], "company": ceo["company"], "status": ceo["status"],
            "headlines": stories, "score": s["score"], "factors": s["factors"],
            "verdict": s["verdict"], "stories": s["stories"],
            "placements": s["placements"],
            "legacy_score": s.get("legacy_score", s["score"]),
            "signal": s.get("signal", "WATCH"),
            "signal_label": s.get("signal_label", ""),
            "newest_days": s.get("newest_days"), "last_seen": s.get("last_seen"),
            "stories_7d": s.get("stories_7d", 0), "stories_30d": s.get("stories_30d", 0),
        })
    provider = scores[0]["provider"] if scores else "Kafka pipeline"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(CORE.render(results, provider, generated, CORE.load_community(os.path.join(os.path.dirname(__file__), ".."))))
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": generated, "provider": provider,
                   "pipeline": "kafka", "results": results,
                   "community": CORE.load_community(os.path.join(os.path.dirname(__file__), ".."))}, f, indent=2)
    print(f"aggregator: wrote {out_dir}/index.html + data.json "
          f"({len(results)} CEOs, {len(clean)} clean stories)")
    return results


if __name__ == "__main__":
    out = "horniness-site"
    argv = sys.argv[1:]
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    t = transport_from_args(argv)
    try:
        run(t, out)
    finally:
        t.close()

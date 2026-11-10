#!/usr/bin/env python3
"""Scorer consumer: headlines.clean -> ceo.scores.

Groups the deduplicated stories per CEO and scores the set with the core
rubric (pipeline-index.py): OpenAI when OPENAI_API_KEY is available —
in CI that is this repo's existing repository secret — otherwise the
deterministic keyword heuristic. Syndication adds a small capped boost:
total placements beyond the story count, +1 per 8 extra placements, max +6.
"""

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.common import (TOPIC_CLEAN, TOPIC_SCORES, load_core,
                                 stage_banner, transport_from_args)

CORE = load_core()


def run(transport):
    stage_banner("scorer")
    clean = transport.consume_all(TOPIC_CLEAN, group="horniness-scorer")
    by_ceo = defaultdict(list)
    for rec in clean:
        by_ceo[rec["ceo"]].append(rec)
    provider = "headline-keyword heuristic (no AI key / AI unavailable)"
    scores = []
    for ceo in CORE.CEOS:
        stories = sorted(by_ceo.get(ceo["name"], []), key=lambda r: -r["reach"])
        placements = sum(s["reach"] for s in stories)
        scored, verdict = None, ""
        try:
            scored = CORE.ai_score(ceo, stories)
        except Exception:
            scored = None
        if scored:
            provider = "Kafka pipeline · AI-scored (OpenAI) from live headlines"
            score, factors = scored["score"], list(scored["factors"])
            verdict = scored["verdict"]
        else:
            score, factors = CORE.heuristic_score(stories)
        boost = min(6, max(0, placements - len(stories)) // 8)
        if boost:
            score = min(99, score + boost)
            factors.append(f"syndication reach: {placements} placements across {len(stories)} stories (+{boost})")
        rec = {"ceo": ceo["name"], "company": ceo["company"], "status": ceo["status"],
               "score": score, "verdict": verdict, "factors": factors,
               "provider": provider, "stories": len(stories), "placements": placements}
        transport.produce(TOPIC_SCORES, ceo["name"], rec)
        scores.append(rec)
        print(f"scorer: {ceo['name']}: {score} ({len(stories)} stories, {placements} placements)")
    transport.flush()
    print(f"scorer: {len(scores)} CEOs -> {TOPIC_SCORES} [{provider}]")
    return scores


if __name__ == "__main__":
    t = transport_from_args(sys.argv[1:])
    try:
        run(t)
    finally:
        t.close()

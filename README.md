# 📈🍆 The Horniness Index

**Live: https://sun-wukhan.github.io/horniness-index/**

A satirical leaderboard scoring public-company CEOs on the risk that their
relationship headlines blow up shareholder value. Born from a meme, built
like a real data pipeline — because the engineering is the joke's delivery
mechanism.

```
 producers (one per source)                dedupe            scorer           aggregator
 ┌────────────────────┐
 │ google-news (RSS)  │──┐
 ├────────────────────┤  │   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ gdelt (global API) │──┼──▶│ headlines.raw│──▶│headlines.clean│──▶│  ceo.scores  │──▶ index.html
 ├────────────────────┤  │   └──────────────┘   └──────────────┘   └──────────────┘    data.json
 │ outlet-rss (BBC…)  │──┘    keyed by CEO       syndicated copies   OpenAI scoring,        │
 └────────────────────┘                         collapsed to reach  heuristic fallback     └─▶ GitHub Pages
```

**Why Kafka for a meme?** Three real properties, not just theatre:

1. **Many sources, one stream.** Each producer polls independently and
   publishes to `headlines.raw`, keyed by CEO name, so a CEO's headlines
   stay together and in order no matter which source found them.
2. **Dedupe as a stream step.** One affair syndicated to 30 outlets is one
   story with reach 30, not 30 stories. The dedupe consumer collapses
   copies (normalised titles + token-overlap) and keeps reach as a capped
   scoring signal.
3. **Replay.** Re-run the scorer over the retained topics under a new
   rubric and history re-grades itself without re-fetching a page.

**Scoring.** Per CEO, the deduplicated headlines go to OpenAI
(`gpt-4o-mini` by default, `HORNINESS_MODEL` to override) with a strict
rubric: score only the supplied headlines, invent nothing, allegations are
allegations. Signals: affairs, workplace relationships, board
investigations, resignations, viral incidents, intelligence angles,
share-price moves, headline volume. Without an API key, a deterministic
keyword heuristic scores instead — the page always builds.

**Running it**

- *CI (the live page):* `.github/workflows/index.yml` runs daily and on
  demand. It starts a single-node Redpanda container (Kafka protocol, no
  ZooKeeper), runs the pipeline, deploys to Pages, and falls back to the
  standalone script (`horniness-index.py`) if the pipeline fails. Add an
  `OPENAI_API_KEY` repository secret to enable AI scoring.
- *Locally with Kafka:* `docker compose up --build`
- *Locally, no broker:* `python3 pipeline/run_pipeline.py --transport file`
  swaps Kafka for JSONL topic files — same stage code, test-harness
  transport.

**Topics**

| Topic | Key | Value |
|---|---|---|
| `headlines.raw` | CEO name | one record per headline found, `via` = which producer |
| `headlines.clean` | CEO name | one record per deduplicated story, with `reach` + `also_in` |
| `ceo.scores` | CEO name | score, verdict, factors, story/placement counts |

Satire, not investment advice. Every score links its source headlines;
several underlying allegations are denied or unproven. Read the sources,
not just the score.

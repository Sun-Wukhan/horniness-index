# 📈🍆 The Horniness Index

**Live: https://sun-wukhan.github.io/horniness-index/**

A satirical leaderboard scoring public-company CEOs on the risk that their
relationship headlines blow up shareholder value. Born from a meme, built
like a real data pipeline — because the engineering is the joke's delivery
mechanism.

```
 producers (one per source)              LLM router        dedupe            scorer           aggregator
 ┌────────────────────┐
 │ google-news (RSS)  │──┐
 ├────────────────────┤  │   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐   ┌──────────────┐
 │ gdelt (global API) │──┼──▶│ headlines.raw│──▶│headlines.routed│──▶│headlines.clean│──▶│  ceo.scores  │──▶ index.html
 ├────────────────────┤  │   └──────────────┘   └───────────────┘   └──────────────┘   └──────────────┘    data.json
 │ outlet-rss (BBC,   │──┤    keyed by CEO      LLM re-tags/drops    syndicated copies   OpenAI scoring,        │
 │  Guardian, NYT…)   │──┘                       misattributed +      collapsed to reach  heuristic fallback     │
 │ ai-scout (OpenAI   │                          off-angle stories                                       │
 │  web search)       │
 └────────────────────┘                                                                                  └─▶ GitHub Pages
```

**The LLM router.** Producers are dumb on purpose; the router
(`pipeline/router.py`) is where headlines get redirected by the LLM:
gpt-4o-mini re-checks every raw headline — which tracked CEO is it
actually about, and is it actually a relationship-angle story? — then
retags it into the right CEO stream or drops it. Without an API key it
degrades to a deterministic surname + keyword fallback.

**Picture checking.** Before render, the aggregator checks every
picture: producer-supplied image URLs must answer 200 with an image
content type (broken ones are dropped), and each CEO's top stories get
the article's own `og:image` when no producer found one.

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


**Roster.** 122 CEOs: a hand-picked hall of legends plus the S&P 500's
top 100 companies by index weight (slickcharts) with their current CEOs
(stockanalysis.com, hand-corrected where the parser grabbed the wrong
executive). Article pictures come from the stories themselves — GDELT
social images, RSS media thumbnails, and an og:image enrichment pass for
each CEO's top story.

**Signals & current heat.** Every CEO carries two numbers. *Current heat*
is the headline score with story points decayed by age (a story today
counts ~12x a year-old one), so the main board answers "what is happening
now": 🔴 **LIVE** (multiple fresh stories this week) · 🟠 **DEVELOPING**
(newest story within a fortnight — possible escalation) · 🟡 **WATCH**
(smoke within 60 days — possible) · ⚪ **COLD CASE**. Cold cases retire to
the Hall of Fame with their *historical peak* (the undecayed score)
preserved. Signals are computed from story dates, not vibes.

**Community nominations (the database is a CSV).** `community.csv` in
this repo is the community database. Anyone can nominate a CEO via the
GitHub Issue Form; when a maintainer adds the `approved` label, the
nomination bot (`.github/workflows/nominations.yml` +
`scripts/append_nomination.py`) appends the row, and the next build shows
it on the site as a 🟡 WATCH community signal. Every entry is a git
commit: auditable, revertable, gloriously low-tech.

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
| `headlines.routed` | CEO name | router-verified headlines — LLM/heuristic confirmed CEO + relationship angle |
| `headlines.clean` | CEO name | one record per deduplicated story, with `reach` + `also_in` |
| `ceo.scores` | CEO name | score, verdict, factors, story/placement counts |

Satire, not investment advice. Every score links its source headlines;
several underlying allegations are denied or unproven. Read the sources,
not just the score.

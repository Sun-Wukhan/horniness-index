#!/usr/bin/env python3
"""Router consumer: headlines.raw -> headlines.routed.

The LLM as traffic controller. Producers are deliberately dumb — RSS
surname matches, search-result overflow, an AI scout with enthusiasm —
so every raw headline passes through here before it may influence a
score. For each record the router decides:

  1. Which tracked CEO is this ACTUALLY about? (Producers mistag: a
     Jensen story surfaces in Bezos' results; "Cook" the verb is not
     Tim Cook.) Retag to the right CEO, or drop if it is about nobody
     on the roster.
  2. Is it actually a relationship/romance-angle headline? A CEO's
     product launch that name-checks his wife in paragraph 9 is not.

With OPENAI_API_KEY set, gpt-4o-mini classifies in batches of 12.
Without a key (or on any failure) a deterministic fallback runs instead:
whole-word surname in the title + relationship keywords. The stage
never hard-fails; worst case it degrades to the fallback for that batch.
"""

import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.common import (TOPIC_RAW, TOPIC_ROUTED, load_core,
                             stage_banner, transport_from_args)

CORE = load_core()
ROSTER = {c["name"]: c for c in CORE.CEOS}
REL = re.compile(r"affair|dating|relationship|divorce|wife|husband|girlfriend|"
                 r"boyfriend|kiss|lover|mistress|romanc|engaged|fianc|wedding|"
                 r"married|spouse|partner|unfaithful|cheat|love|scandal|"
                 r"ex-wife|ex-husband|harassment", re.I)
BATCH = 12

ROUTE_PROMPT = """You route headlines for a satirical CEO leaderboard. For each numbered
headline decide:
- "ceo": the tracked CEO the headline is ACTUALLY about — one of the roster
  names below, spelled exactly, or null if it is about none of them.
- "rel": true only if the headline is about a romantic relationship angle
  (affair, dating, divorce, spouse, engagement, workplace romance, related
  scandal/investigation). Business news that merely mentions a spouse is
  false — and so is a profile of the CEO's spouse, philanthropy, family or
  lifestyle with no romantic-risk angle (a "meet the philanthropist wife"
  puff piece is not a signal).

Reply with ONLY a JSON array: [{{"i": <n>, "ceo": <name or null>, "rel": <bool>}}]

Roster: {roster}

Headlines:
{lines}
"""


def heuristic_route(rec):
    title = rec.get("title", "")
    surname = (rec.get("ceo", "").split() or [""])[-1].lower()
    if surname and re.search(r"\b" + re.escape(surname) + r"\b", title.lower()) \
            and REL.search(title):
        return rec.get("ceo"), True
    # Maybe another CEO's surname headlines it (cross-tagged by a producer)
    for name in ROSTER:
        sn = name.split()[-1].lower()
        if len(sn) >= 4 and re.search(r"\b" + re.escape(sn) + r"\b", title.lower()) \
                and REL.search(title):
            return name, True
    return None, False


def llm_route(batch):
    """Returns {index_in_batch: (ceo_name_or_None, rel_bool)} or None on failure."""
    lines = "\n".join(f'[{i}] (tagged: {r.get("ceo", "?")}) {r.get("title", "")}'
                      for i, r in enumerate(batch))
    prompt = ROUTE_PROMPT.format(roster=", ".join(ROSTER), lines=lines)
    body = json.dumps({
        "model": os.environ.get("HORNINESS_MODEL") or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"content-type": "application/json",
                 "authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    text = payload["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip()
    lo, hi = text.find("["), text.rfind("]")
    if lo >= 0 and hi > lo:
        text = text[lo:hi + 1]
    out = {}
    for item in json.loads(text):
        ceo = item.get("ceo")
        out[int(item["i"])] = (ceo if ceo in ROSTER else None,
                               bool(item.get("rel")))
    return out


def run(transport):
    stage_banner("router (LLM-directed)")
    raw = transport.consume_all(TOPIC_RAW, group="horniness-router")
    use_llm = bool(os.environ.get("OPENAI_API_KEY"))
    batches = [raw[i:i + BATCH] for i in range(0, len(raw), BATCH)]

    def decide(batch):
        verdicts = None
        if use_llm:
            try:
                verdicts = llm_route(batch)
            except Exception:
                verdicts = None
        out = []
        for i, rec in enumerate(batch):
            if verdicts is not None and i in verdicts:
                ceo, rel = verdicts[i]
                mode = "llm"
            else:
                ceo, rel = heuristic_route(rec)
                mode = "heuristic"
            out.append((rec, ceo, rel, mode))
        return out

    with ThreadPoolExecutor(max_workers=8) as ex:
        decided = [d for batch_out in ex.map(decide, batches) for d in batch_out]

    kept = dropped = retagged = 0
    modes = {"llm": 0, "heuristic": 0}
    for rec, ceo, rel, mode in decided:
        modes[mode] += 1
        if not rel or not ceo:
            dropped += 1
            continue
        if ceo != rec.get("ceo"):
            retagged += 1
            meta = ROSTER[ceo]
            rec = {**rec, "ceo": ceo, "company": meta["company"],
                   "status": meta["status"]}
        rec["routed_by"] = mode
        transport.produce(TOPIC_ROUTED, ceo, rec)
        kept += 1
    transport.flush()
    print(f"router: {len(raw)} raw -> {kept} routed "
          f"(dropped {dropped}, retagged {retagged}; "
          f"llm {modes['llm']}, heuristic {modes['heuristic']})")
    return kept


if __name__ == "__main__":
    t = transport_from_args(sys.argv[1:])
    try:
        run(t)
    finally:
        t.close()

#!/usr/bin/env python3
"""The Horniness Index — a satirical leaderboard for public-company CEOs.

Fetches real news headlines per CEO from Google News RSS, then scores the
*risk that romantic/relationship headlines blow up shareholder value*.

Provider selection (same pattern as ci/ai-triage.py):
  1. OPENAI_API_KEY set -> OpenAI Chat Completions (gpt-4o-mini by default;
     override with HORNINESS_MODEL) returns a JSON score + verdict per CEO.
  2. No key / AI call fails -> deterministic headline-keyword heuristic.
     The page always builds; it never fails on AI or network errors alone
     (a CEO with no fetched headlines gets a low score and a note).

Satire guardrails baked into the AI prompt and the page:
  - Score ONLY the headlines supplied. Invent nothing.
  - Attribute allegations as allegations. Unproven claims are wording in a
    headline, not established fact.
  - The joke is shareholder risk, not a claim about anyone's private life
    beyond what the headlines themselves report.

Usage: python3 pipeline-index.py [output_dir]
Output: <output_dir>/index.html and <output_dir>/data.json
"""

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

CEOS = [
    {
        "name": "Jensen Huang",
        "company": "NVIDIA",
        "status": "CEO, in post",
        "queries": [
            '"Jensen Huang" affair',
            '"Jensen Huang" relationship spy investigation',
        ],
    },
    {
        "name": "Andy Byron",
        "company": "Astronomer",
        "status": "CEO, resigned Jul 2025",
        "queries": [
            '"Andy Byron" Astronomer Coldplay affair',
            '"Andy Byron" CEO resigned kiss cam',
        ],
    },
    {
        "name": "Elon Musk",
        "company": "Tesla",
        "status": "CEO, in post",
        "queries": [
            '"Elon Musk" girlfriend OR dating OR relationship',
            '"Elon Musk" children relationship headlines',
        ],
    },
    {
        "name": "Steve Easterbrook",
        "company": "McDonald's",
        "status": "CEO, fired 2019",
        "queries": [
            '"Steve Easterbrook" McDonald\'s relationship fired',
        ],
    },
    {
        "name": "Brian Krzanich",
        "company": "Intel",
        "status": "CEO, resigned 2018",
        "queries": [
            '"Brian Krzanich" Intel resigned relationship employee',
        ],
    },
    {
        "name": "Jeff Bezos",
        "company": "Amazon",
        "status": "Founder / former CEO",
        "queries": [
            '"Jeff Bezos" divorce settlement affair headlines',
        ],
    },
    {
        "name": "Tim Cook",
        "company": "Apple",
        "status": "CEO, in post",
        "queries": [
            '"Tim Cook" dating OR relationship OR affair',
        ],
    },
    {
        "name": "Satya Nadella",
        "company": "Microsoft",
        "status": "CEO, in post",
        "queries": [
            '"Satya Nadella" wife OR relationship OR affair',
        ],
    },
]

MAX_HEADLINES_PER_CEO = 10

# Heuristic weights: (keyword regex, points, label)
SIGNALS = [
    (r"\bspy\b|espionage|intelligence agenc|honey ?trap", 25, "foreign-intelligence angle"),
    (r"\baffair\b|extramarital|unfaithful|cheat", 18, "affair headlines"),
    (r"resign|fired|ousted|steps down|step down", 18, "lost / risked the job over it"),
    (r"investigat|board.*probe|inquiry", 12, "board / official investigation"),
    (r"kiss cam|viral|caught on", 12, "viral public incident"),
    (r"employee|subordinate|\bHR\b|executive.*relationship", 12, "workplace-relationship angle"),
    (r"shares? (fell|plunge|drop|slump)|stock (fell|plunge|drop)", 10, "share price moved"),
    (r"divorce|settlement", 10, "divorce / settlement headlines"),
    (r"dating|girlfriend|boyfriend|relationship|romantic", 6, "dating / relationship headlines"),
    (r"children|child with", 3, "children in the headlines"),
]


def fetch_headlines(queries):
    seen, out = set(), []
    for q in queries:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
               + "&hl=en-US&gl=US&ceid=US:en")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=25).read()
            root = ET.fromstring(data)
        except Exception:
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else ""
            key = re.sub(r"\W+", " ", title.lower()).strip()
            if not title or key in seen:
                continue
            seen.add(key)
            out.append({"title": title, "link": link, "date": pub, "source": source})
            if len(out) >= MAX_HEADLINES_PER_CEO:
                return out
    return out


STRONG = re.compile(r"affair|extramarital|unfaithful|resign|fired|ousted|kiss cam|"
                    r"spy|espionage|divorce|investigat|broke up|dumped|ex-girlfriend|"
                    r"ex-boyfriend|lover|mistress|mother of .* children", re.I)


def heuristic_score(headlines):
    if not headlines:
        return 3, ["no relationship headlines found — deeply boring, shareholder-safe"]
    text = " \n".join(h["title"].lower() for h in headlines)
    score, factors = 0, []
    for pattern, points, label in SIGNALS:
        hits = len(re.findall(pattern, text))
        if hits:
            gained = points if hits == 1 else int(points * 1.5)
            score += gained
            factors.append(f"{label} (x{hits})")
    score += min(len(headlines), 10)  # headline volume
    # Dampen when few headlines carry a strong signal — e.g. "relationship"
    # headlines that turn out to be about a CEO's relationship with a
    # government or with AI. (The AI scorer reads this properly; the
    # heuristic is only a fallback.)
    strong = sum(1 for h in headlines if STRONG.search(h["title"]))
    score = int(score * (0.35 + 0.65 * min(strong / 4, 1)))
    return max(2, min(score, 99)), factors[:5]


AI_PROMPT = """You are scoring a SATIRICAL "Horniness Index" for a meme page: the risk that a
public-company CEO's romantic/relationship headlines blow up shareholder value.

Score ONLY from the headlines supplied below. Do not invent facts, people, or
events. Treat every allegation in a headline as an allegation, not a fact.
Weigh, roughly: a documented affair/relationship with a colleague or subordinate,
a board investigation, a resignation/firing, a viral public incident, a foreign
intelligence angle, a costly divorce, and sheer headline volume. A CEO with
almost no relationship headlines scores near 0.

Reply with ONLY a JSON object, no markdown:
{{"score": <int 0-100>, "verdict": "<one funny, one-sentence summary of what the HEADLINES say, max 25 words>", "factors": ["<short factor>", "... up to 4"]}}

CEO: {name} ({company}, {status})
Headlines:
{headlines}
"""


def ai_score(ceo, headlines):
    if not headlines or not os.environ.get("OPENAI_API_KEY"):
        return None
    joined = "\n".join(f"- {h['title']}" for h in headlines)
    prompt = AI_PROMPT.format(name=ceo["name"], company=ceo["company"],
                              status=ceo["status"], headlines=joined)
    body = json.dumps({
        "model": os.environ.get("HORNINESS_MODEL") or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"content-type": "application/json",
                 "authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    text = payload["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip()
    data = json.loads(text)
    return {"score": max(0, min(int(data["score"]), 100)),
            "verdict": str(data.get("verdict", ""))[:220],
            "factors": [str(f)[:90] for f in data.get("factors", [])][:4]}


def risk_label(score):
    if score >= 80: return "☢️ BOARD-LEVEL EMERGENCY"
    if score >= 60: return "🔥 SEVERE — shareholders sweating"
    if score >= 40: return "🌡️ ELEVATED — PR team on standby"
    if score >= 20: return "😐 SIMMERING — mostly gossip"
    return "🧊 ICE COLD — married to the job"


def render(results, provider, generated):
    rows = []
    for i, r in enumerate(sorted(results, key=lambda x: -x["score"]), 1):
        heads = "".join(
            f'<li><a href="{html.escape(h["link"])}" target="_blank" rel="noopener">'
            f'{html.escape(h["title"])}</a></li>' for h in r["headlines"][:5])
        factors = "".join(f"<li>{html.escape(f)}</li>" for f in r["factors"])
        verdict = html.escape(r["verdict"]) if r["verdict"] else ""
        rows.append(f"""
<article class="card">
  <div class="rank">#{i}</div>
  <div class="main">
    <h2>{html.escape(r['name'])} <span class="co">{html.escape(r['company'])} · {html.escape(r['status'])}</span></h2>
    <div class="meter"><div class="bar" style="width:{r['score']}%"></div><span class="score">{r['score']}/100</span></div>
    <p class="risk">{risk_label(r['score'])}</p>
    {f'<p class="verdict">“{verdict}”</p>' if verdict else ''}
    <details><summary>Why — signals &amp; top headlines</summary>
      <ul class="factors">{factors}</ul><ul class="heads">{heads}</ul></details>
  </div>
</article>""")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Horniness Index — CEO Shareholder-Risk Leaderboard</title>
<style>
body{{background:#111;color:#eee;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:860px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:2.2rem;margin-bottom:4px}} .sub{{color:#bbb;margin-top:0}}
.card{{display:flex;gap:16px;background:#1c1c22;border:1px solid #333;border-radius:14px;padding:18px;margin:14px 0}}
.rank{{font-size:1.6rem;font-weight:800;color:#ff5c8a;min-width:44px}}
.co{{color:#999;font-size:.85rem;font-weight:400}} h2{{margin:0 0 10px;font-size:1.25rem}}
.meter{{position:relative;background:#333;border-radius:8px;height:22px;overflow:hidden}}
.bar{{height:100%;background:linear-gradient(90deg,#ffb347,#ff5c8a,#c9184a)}}
.score{{position:absolute;right:8px;top:2px;font-weight:700;font-size:.9rem}}
.risk{{font-weight:700;margin:10px 0 4px}} .verdict{{color:#ffd6e0;font-style:italic;margin:4px 0}}
details{{margin-top:8px}} summary{{cursor:pointer;color:#8ecae6}}
ul{{margin:6px 0}} a{{color:#a8d8ff}} .fine{{color:#888;font-size:.82rem;line-height:1.5;margin-top:28px}}
</style></head><body>
<h1>📈🍆 The Horniness Index</h1>
<p class="sub">Tracking public-company CEOs, their relationship headlines, and the risk of
blowing up shareholder value by being too horny. Updated {generated} · Scoring: {provider}</p>
{''.join(rows)}
<p class="fine"><strong>Methodology (such as it is):</strong> for each CEO we pull live headlines from
Google News RSS matching relationship keywords, then score them — by AI (OpenAI, using this repo's
existing <code>OPENAI_API_KEY</code> secret) when available, otherwise by a deterministic keyword
heuristic. The score measures <em>headline risk to shareholder value</em>: affairs, workplace
relationships, board investigations, resignations, viral incidents, and share-price moves.</p>
<p class="fine"><strong>Satire &amp; sourcing:</strong> this is a meme, not investment advice and not a
factual dossier. Every score is derived only from the linked headlines; allegations in those
headlines are allegations — several are denied or unproven, and at least one board has said it has
no reason to believe any secrets were compromised. Read the sources, not just the score.</p>
<p class="fine">A loving tribute to the genre. Not affiliated with any company named. No CEOs were consulted.</p>
</body></html>"""


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "horniness-index"
    os.makedirs(out_dir, exist_ok=True)
    results, provider = [], "headline-keyword heuristic (no AI key / AI unavailable)"
    for ceo in CEOS:
        headlines = fetch_headlines(ceo["queries"])
        scored, verdict = None, ""
        try:
            scored = ai_score(ceo, headlines)
        except Exception:
            scored = None
        if scored:
            provider = "AI-scored (OpenAI) from live headlines"
            score, factors, verdict = scored["score"], scored["factors"], scored["verdict"]
        else:
            score, factors = heuristic_score(headlines)
        results.append({**ceo, "headlines": headlines, "score": score,
                        "factors": factors, "verdict": verdict})
        print(f"{ceo['name']}: {score} from {len(headlines)} headlines")
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render(results, provider, generated))
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": generated, "provider": provider, "results": results}, f, indent=2)
    print(f"Wrote {out_dir}/index.html and data.json ({provider})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

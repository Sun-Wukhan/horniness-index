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
from email.utils import parsedate_to_datetime

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
    {
        "name": "Bill Gates",
        "company": "Microsoft",
        "status": "Co-founder, former CEO",
        "queries": [
            '"Bill Gates" affair employee board inquiry',
            '"Bill Gates" divorce Melinda relationship Epstein questions',
        ],
    },
    {
        "name": "Eric Schmidt",
        "company": "Google",
        "status": "Former CEO",
        "queries": [
            '"Eric Schmidt" girlfriend relationship dating',
            '"Eric Schmidt" open marriage mistress headlines',
        ],
    },
    {
        "name": "Larry Ellison",
        "company": "Oracle",
        "status": "Co-founder, exec chairman",
        "queries": [
            '"Larry Ellison" wife divorce girlfriend',
            '"Larry Ellison" dating relationship wedding',
        ],
    },
    {
        "name": "Rupert Murdoch",
        "company": "News Corp / Fox",
        "status": "Former chairman",
        "queries": [
            '"Rupert Murdoch" engagement called off fiancee',
            '"Rupert Murdoch" divorce wife marriage',
        ],
    },
    {
        "name": "Mark Hurd",
        "company": "HP",
        "status": "Former CEO — resigned 2010",
        "queries": [
            '"Mark Hurd" HP resignation relationship contractor',
            '"Mark Hurd" Jodie Fisher scandal',
        ],
    },
    {
        "name": "Brian Dunn",
        "company": "Best Buy",
        "status": "Former CEO — resigned 2012",
        "queries": [
            '"Brian Dunn" Best Buy relationship employee resignation',
        ],
    },
    {
        "name": "Harry Stonecipher",
        "company": "Boeing",
        "status": "Former CEO — resigned 2005",
        "queries": [
            '"Harry Stonecipher" Boeing affair resigned code of conduct',
        ],
    },
    {
        "name": "Travis Kalanick",
        "company": "Uber",
        "status": "Co-founder, former CEO — resigned 2017",
        "queries": [
            '"Travis Kalanick" girlfriend relationship breakup',
            '"Travis Kalanick" Gabi Holzwarth',
        ],
    },
    {
        "name": "Jack Dorsey",
        "company": "Block / Twitter",
        "status": "Co-founder, CEO of Block",
        "queries": [
            '"Jack Dorsey" dating girlfriend relationship',
            '"Jack Dorsey" model girlfriend headlines',
        ],
    },
    {
        "name": "Mark Zuckerberg",
        "company": "Meta",
        "status": "CEO, in post",
        "queries": [
            '"Mark Zuckerberg" wife Priscilla relationship',
        ],
    },
    {
        "name": "Sundar Pichai",
        "company": "Alphabet / Google",
        "status": "CEO, in post",
        "queries": [
            '"Sundar Pichai" wife relationship',
        ],
    },
]

# --- S&P 500 top-100 CEOs (by index weight) -------------------------------
# Built 2026-11-10: top 100 from slickcharts' S&P 500 weights, CEO names
# from stockanalysis.com company pages, with manual corrections where the
# parser grabbed the wrong executive (AAPL, DHR, COP, ETN, MO) and for
# Netflix's co-CEO. Statuses are generic; the headlines decide the rest.
_SP500_CEOS = [
    ("Tim Cook", "Apple"),
    ("Jensen Huang", "Nvidia Corp"),
    ("Satya Nadella", "Microsoft Corp"),
    ("Andrew Jassy", "Amazon"),
    ("Sundar Pichai", "Alphabet / Google"),
    ("Mark Zuckerberg", "Meta"),
    ("Hock Tan", "Broadcom"),
    ("Elon Musk", "Tesla"),
    ("David Ricks", "Eli Lilly"),
    ("Gregory Abel", "Berkshire Hathaway"),
    ("John Furner", "Walmart"),
    ("James Dimon", "JPMorgan Chase"),
    ("Lisa Su", "Advanced Micro Devices"),
    ("Sanjay Mehrotra", "Micron Technology"),
    ("Ryan McInerney", "Visa"),
    ("Joaquin Duato", "Johnson & Johnson"),
    ("Darren Woods", "ExxonMobil Holdings"),
    ("Michael Miebach", "Mastercard Incorporated"),
    ("Robert Michael", "AbbVie"),
    ("Ron Vachris", "Costco Wholesale Corp"),
    ("Henrique Braun", "Coca-Cola Company"),
    ("Michael Wirth", "Chevron"),
    ("Alexander Karp", "Palantir Technologies"),
    ("Shailesh Jejurikar", "Procter & Gamble Company"),
    ("Charles Robbins", "Cisco Systems"),
    ("Safra Catz", "Oracle Corp"),
    ("Robert Davis", "Merck & Co."),
    ("Stephen Hemsley", "UnitedHealth"),
    ("Brian Moynihan", "Bank of America"),
    ("Edward Decker", "Home Depot"),
    ("Larry Culp", "GE Aerospace"),
    ("Lip-Bu Tan", "Intel Corp"),
    ("Jacek Olczak", "Philip Morris International"),
    ("Gary Dickerson", "Applied Materials Inc"),
    ("Edward Pick", "Morgan Stanley"),
    ("Timothy Archer", "Lam Research Corp"),
    ("Haviv Ilan", "Texas Instruments Incorporated"),
    ("Nikesh Arora", "Palo Alto Networks"),
    ("Christopher Calio", "Rtx"),
    ("Joseph Creed", "Caterpillar"),
    ("Marc Casper", "Thermo Fisher Scientific"),
    ("David Solomon", "Goldman Sachs Group"),
    ("Charles Scharf", "Wells Fargo & Co."),
    ("Ted Sarandos", "NetFlix Inc"),
    ("Robert Bradway", "Amgen Inc"),
    ("Jayshree Ullal", "Arista Networks"),
    ("Sanjiv Lamba", "Linde plc Ordinary Share"),
    ("George Kurtz", "CrowdStrike Holdings"),
    ("Arvind Krishna", "International Business Machines"),
    ("Stephen Squeri", "American Express Company"),
    ("Michael Dell", "Dell Technologies"),
    ("Daniel O'Day", "Gilead Sciences Inc"),
    ("Scott Strazik", "GE Vernova"),
    ("Josh D’Amaro", "The Walt Disney Company"),
    ("Jane Fraser", "Citigroup"),
    ("Richard Wallace", "Kla"),
    ("Adam Norwitt", "Amphenol"),
    ("Christopher Kempczinski", "McDonald's"),
    ("Ramon Laguarta", "PepsiCo"),
    ("Daniel Schulman", "Verizon Communications"),
    ("Vincent Roche", "Analog Devices"),
    ("Robert Ford", "Abbott Laboratories"),
    ("Laurence Fink", "Blackrock"),
    ("Cristiano Amon", "Qualcomm Inc"),
    ("John May", "Deere & Company"),
    ("John Ketchum", "NextEra Energy"),
    ("Shankh Mitra", "Welltower"),
    ("Srinivasan Gopalan", "T-Mobile US"),
    ("Marc Benioff", "Salesforce"),
    ("Vincenzo Vena", "Union Pacific Corp."),
    ("Albert Bourla", "Pfizer"),
    ("Rainer Blair", "Danaher"),
    ("Richard Wurster", "The Charles Schwab"),
    ("David Rosa", "Intuitive Surgical"),
    ("Ryan Lance", "ConocoPhillips"),
    ("Ernie Herrman", "TJX Companies"),
    ("Julie T. Sweet", "Accenture PLC"),
    ("John Stankey", "At&T"),
    ("Dara Khosrowshahi", "Uber Technologies"),
    ("Matthew Murphy", "Marvell Technology"),
    ("Evan Greenberg", "Chubb Limited"),
    ("Reshma Kewalramani", "Vertex Pharmaceuticals Inc"),
    ("Craig Arnold", "Eaton Corporation, plc"),
    ("James Taiclet", "Lockheed Martin Corp."),
    ("Robert Ortberg", "Boeing Company"),
    ("Martina Cheung", "S&P Global"),
    ("Adam Foroughi", "Applovin"),
    ("Susan Griffith", "Progressive"),
    ("Geoffrey Martha", "Medtronic plc"),
    ("Marvin Ellison", "Lowe's Companies"),
    ("David V. Goeckeler", "Sandisk"),
    ("Billy Gifford", "Altria Group"),
    ("Christopher Boerner", "Bristol-Myers Squibb Co."),
    ("Natascha Viljoen", "Newmont"),
    ("Brian Niccol", "Starbucks Corp"),
    ("Daniel Letter", "Prologis"),
    ("Brian Tyler", "McKesson"),
    ("Kevin Lobo", "Stryker"),
    ("Glenn Fogel", "Booking Holdings"),
    ("Jennifer A. Parmentier", "Parker-Hannifin"),
    ("Richard Fairbank", "Capital One Financial"),
    ("Maria Black", "Automatic Data Processing"),
    ("William Mosley", "Seagate Technology Holdings PLC Ordinary Shares (Ireland)"),
    ("Brian Chesky", "Airbnb"),
    ("David Joyner", "CVS Health"),
    ("Tiang Yew Tan", "Western Digital Corp."),
    ("Terrence Duffy", "CME Group"),
    ("Christopher Womack", "The Southern Company"),
    ("Anthony Capuano", "Marriott International")
]
_seen_names = {c["name"].lower() for c in CEOS}
for _name, _company in _SP500_CEOS:
    if _name.lower() not in _seen_names:
        _seen_names.add(_name.lower())
        CEOS.append({
            "name": _name, "company": _company,
            "status": "CEO, in post (S&P 500 top 100)",
            "queries": [f'"{_name}" affair OR relationship OR divorce OR dating OR wife OR husband'],
        })

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
            desc = item.findtext("description") or ""
            imgm = re.search(r'<img[^>]+src="([^"]+)"', desc)
            out.append({"title": title, "link": link, "date": pub, "source": source,
                        "image": imgm.group(1) if imgm else ""})
            if len(out) >= MAX_HEADLINES_PER_CEO:
                return out
    return out


STRONG = re.compile(r"affair|extramarital|unfaithful|resign|fired|ousted|kiss cam|"
                    r"spy|espionage|divorce|investigat|broke up|dumped|ex-girlfriend|"
                    r"ex-boyfriend|lover|mistress|mother of .* children", re.I)


def parse_date(s):
    """Best-effort parse of RSS pubDate, GDELT seendate, or ISO strings.

    ALWAYS returns a tz-aware datetime (or None). The fromisoformat branch
    used to leak naive datetimes for date-only strings like "2026-11-08"
    (exactly what the AI scout emits) — that crashed the Kafka scorer in
    CI run 42836635711 with "can't subtract offset-naive and offset-aware".
    """
    if not s:
        return None
    s = str(s).strip()
    dt = None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$", s)
    if m:
        try:
            dt = datetime(*[int(x) for x in m.groups()], tzinfo=timezone.utc)
        except ValueError:
            dt = None
    if dt is None:
        try:
            dt = parsedate_to_datetime(s)
        except Exception:
            dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            dt = None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(date_str, now=None):
    dt = parse_date(date_str)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def recency_weight(age):
    """How much a story of this age counts toward CURRENT heat."""
    if age is None:
        return 0.3
    if age <= 3: return 1.0
    if age <= 14: return 0.85
    if age <= 45: return 0.6
    if age <= 120: return 0.35
    if age <= 365: return 0.18
    return 0.08


def compute_signals(headlines, now=None):
    """Signal state from story dates: what is happening NOW vs history.

    LIVE       - fresh stories in the last 72h, more than one of them
    DEVELOPING - newest story within 14 days (possible escalation)
    WATCH      - newest within 60 days (early smoke, possible)
    COLD       - nothing recent: a historical case, kept for the record
    """
    now = now or datetime.now(timezone.utc)
    ages = [a for a in (age_days(h.get("date"), now) for h in headlines)
            if a is not None]
    newest = min(ages) if ages else None
    last_seen = None
    if headlines and newest is not None:
        freshest = min(headlines,
                       key=lambda h: age_days(h.get("date"), now)
                       if age_days(h.get("date"), now) is not None else 1e9)
        dt = parse_date(freshest.get("date"))
        last_seen = dt.strftime("%Y-%m-%d") if dt else None
    s7 = sum(1 for a in ages if a <= 7)
    s30 = sum(1 for a in ages if a <= 30)
    if newest is not None and newest <= 3 and s7 >= 2:
        signal, label = "LIVE", "\U0001F534 LIVE \u2014 happening now"
    elif newest is not None and newest <= 14:
        signal, label = "DEVELOPING", "\U0001F7E0 DEVELOPING \u2014 unfolding, possible escalation"
    elif newest is not None and newest <= 60:
        signal, label = "WATCH", "\U0001F7E1 WATCH \u2014 early smoke, possible"
    else:
        signal, label = "COLD", "\u26AA COLD CASE \u2014 historical, for the record"
    return {"signal": signal, "signal_label": label,
            "newest_days": (round(newest, 1) if newest is not None else None),
            "last_seen": last_seen, "stories_7d": s7, "stories_30d": s30}


def heat_score(headlines, now=None):
    """Recency-weighted score: the same rubric as the legacy score, but a
    story's points decay with its age, so the number measures what is
    happening NOW rather than the worst week of someone's life in 2019."""
    if not headlines:
        return 3, ["no relationship headlines found \u2014 deeply boring, shareholder-safe"]
    now = now or datetime.now(timezone.utc)
    total, wsum, strong_w, gains = 0.0, 0.0, 0.0, {}
    for h in headlines:
        w = recency_weight(age_days(h.get("date"), now))
        wsum += w
        if STRONG.search(h["title"]):
            strong_w += w
        t = h["title"].lower()
        for pattern, points, label in SIGNALS:
            if re.search(pattern, t):
                total += points * w
                gains[label] = gains.get(label, 0.0) + points * w
    total += min(wsum, 10)
    total *= (0.35 + 0.65 * min(strong_w / 4.0, 1))
    factors = [f"{k} (current-weighted)" for k, _ in
               sorted(gains.items(), key=lambda kv: -kv[1])[:4]]
    sig = compute_signals(headlines, now)
    factors.append(f"freshness: newest story {sig['newest_days']}d ago, "
                   f"{sig['stories_7d']} in the last week")
    return max(2, min(int(round(total)), 99)), factors[:5]


def legacy_heuristic_score(headlines):
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

Score the CURRENT risk, not the historical worst case: headlines carry
dates \u2014 weight the last 30 days heavily. An old, resolved story that is
not back in the news counts for little; a story moving right now counts
double. If everything supplied is old, say so in the verdict.

Reply with ONLY a JSON object, no markdown:
{{"score": <int 0-100>, "verdict": "<one funny, one-sentence summary of what the HEADLINES say, max 25 words>", "factors": ["<short factor>", "... up to 4"]}}

CEO: {name} ({company}, {status})
Headlines:
{headlines}
"""


def ai_score(ceo, headlines):
    if not headlines or not os.environ.get("OPENAI_API_KEY"):
        return None
    joined = "\n".join(f"- [{(h.get('date') or 'undated')}] {h['title']}" for h in headlines)
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
    if score >= 90: return "☢️ DEFCON 1 — HR, Legal and the board have a group chat"
    if score >= 80: return "🔥 BOARD-LEVEL EMERGENCY — the group chat has lawyers now"
    if score >= 60: return "🥵 SEVERE — PR is drafting statements 'just in case'"
    if score >= 40: return "🌡️ ELEVATED — comms has a folder with his name on it"
    if score >= 20: return "😐 SIMMERING — gossip, mostly harmless"
    return "🧊 ICE COLD — married to the job, and the job is winning"


def band_verdict(score):
    """Snark for when there is no AI verdict to quote."""
    if score >= 90: return "The headlines read like a board pack marked URGENT."
    if score >= 80: return "Somewhere, a general counsel just felt a disturbance."
    if score >= 60: return "The tabloids know his hotel preferences. Allegedly."
    if score >= 40: return "Nothing proven. Everything screenshotted."
    if score >= 20: return "Mostly smoke, and the smoke is just vape."
    return "Cleaner than the company's own code of conduct."


def fetch_og_image(url):
    """The article's own picture (og:image / twitter:image), if it has one."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            if "html" not in (resp.headers.get("Content-Type") or ""):
                return ""
            data = resp.read(262144).decode("utf-8", "replace")
        m = (re.search(r'property="og:image"\s+content="([^"]+)"', data)
             or re.search(r'name="twitter:image"\s+content="([^"]+)"', data)
             or re.search(r'content="([^"]+)"\s+property="og:image"', data))
        return m.group(1) if m else ""
    except Exception:
        return ""


def image_ok(url):
    """Is this picture actually there? 200 + an image content type."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            ctype = resp.headers.get("Content-Type") or ""
            return resp.status in (200, 206) and ctype.startswith("image/")
    except Exception:
        return False


def enrich_images(results, workers=12):
    """Picture pass, in two phases — every picture on the page is checked.

    Phase 1 validates producer-supplied pictures: dead or non-image URLs
    are dropped from their story. Phase 2 gives each CEO's top 3 stories
    a picture via the article's own og:image when they have none (Google
    RSS carries no images; GDELT is often throttled). Best-effort
    throughout — a missing picture never blocks the page.
    Returns {"valid": n, "dropped": n, "enriched": n}."""
    from concurrent.futures import ThreadPoolExecutor
    stats = {"valid": 0, "dropped": 0, "enriched": 0}
    holders = {}
    for r in results:
        for h in r["headlines"]:
            if h.get("image"):
                holders.setdefault(h["image"], []).append(h)
    if holders:
        urls = list(holders)[:600]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            oks = list(ex.map(image_ok, urls))
        for url, ok in zip(urls, oks):
            if ok:
                stats["valid"] += 1
            else:
                stats["dropped"] += 1
                for h in holders[url]:
                    h["image"] = ""
    jobs = {}
    for r in results:
        missing = 0
        for h in r["headlines"]:
            if missing >= 3:
                break
            link = h.get("link") or ""
            if not h.get("image") and link.startswith("http") \
                    and "news.google.com" not in link:
                jobs[id(h)] = (h, link)
                missing += 1
    if jobs:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            imgs = list(ex.map(lambda kv: fetch_og_image(kv[1][1]), jobs.items()))
        for (h, _link), img in zip(list(jobs.values()), imgs):
            if img:
                h["image"] = img
                stats["enriched"] += 1
    return stats


def load_community(base_dir):
    """The community database: community.csv in the repo root."""
    import csv
    path = os.path.join(base_dir, "community.csv")
    rows = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("ceo_name") or "").strip():
                    rows.append({k: (v or "").strip() for k, v in row.items()})
    except FileNotFoundError:
        pass
    return list(reversed(rows))  # newest nomination first


def render(results, provider, generated, community=None):
    results = sorted(results, key=lambda x: -x["score"])
    now_list = [r for r in results if r.get("signal") != "COLD"]
    hall_list = sorted((r for r in results if r.get("signal") == "COLD"),
                       key=lambda x: -x.get("legacy_score", x["score"]))
    top_live = now_list[0] if now_list else (results[0] if results else None)
    total_stories = sum(len(r["headlines"]) for r in results)
    live_count = sum(1 for r in results if r.get("signal") == "LIVE")
    avg = round(sum(r["score"] for r in results) / len(results)) if results else 0
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    def card(r, i, hall=False):
        def _thumb(h):
            if h.get("image"):
                return (f'<img class="thumb" src="{html.escape(h["image"])}" alt="" '
                        f'loading="lazy" onerror="this.remove()">')
            return ""
        heads = "".join(
            f'<li>{_thumb(h)}<a href="{html.escape(h["link"])}" target="_blank" rel="noopener">'
            f'{html.escape(h["title"])}</a></li>' for h in r["headlines"][:5])
        feat_h = next((h for h in r["headlines"] if h.get("image")), None)
        feat = (f'<img class="feat" src="{html.escape(feat_h["image"])}" alt="" '
                f'loading="lazy" onerror="this.remove()">') if feat_h else ""
        factors = "".join(f"<li>{html.escape(f)}</li>" for f in r["factors"])
        verdict = html.escape(r["verdict"]) if r["verdict"] else band_verdict(r["score"])
        shown = r.get("legacy_score", r["score"]) if hall else r["score"]
        label = "all-time peak" if hall else "current heat"
        medal = medals.get(i, "") if not hall else ""
        crown = (' <span class="crown">👑 CHIEF HORNY OFFICER</span>'
                 if not hall and i == 1 else "")
        if not hall and r["name"] == "Jensen Huang":
            crown += ' <span class="chip">📌 PINNED #1 — man of the hour (editorial override)</span>'
        pulse = ' <span class="pulse"></span>' if r.get("signal") == "LIVE" else ""
        last = ""
        if r.get("last_seen"):
            nd = r.get("newest_days")
            ago = f" ({nd}d ago)" if nd is not None else ""
            last = f'<span class="chip">🗞️ last story {html.escape(str(r["last_seen"]))}{ago}</span> '
        reach = ""
        if r.get("stories") is not None:
            reach = (f'<p class="reach">🗞️ {r["stories"]} distinct stories · '
                     f'{r.get("placements", r["stories"])} total placements '
                     f'(syndication counts — one scandal, everywhere)</p>')
        return f"""
<article class="card{' top' if not hall and i == 1 else ''}">
  <div class="rank">{medal or '#' + str(i)}</div>
  <div class="main">
    {feat}
    <h2>{html.escape(r['name'])}{crown} <span class="co">{html.escape(r['company'])} · {html.escape(r['status'])}</span></h2>
    <p class="sig">{pulse}{html.escape(r.get('signal_label', ''))}</p>
    <div class="meter"><div class="bar" style="width:{shown}%"></div><span class="score">{shown}/100 · {label}</span></div>
    <p class="risk">{risk_label(shown)}</p>
    <p class="verdict">“{verdict}”</p>
    <p>{last}{'<span class="chip">📚 historical peak ' + str(r.get('legacy_score', r['score'])) + '/100</span>' if not hall and r.get('legacy_score') is not None and r.get('legacy_score', 0) > r['score'] else ''}</p>
    {reach}
    <details><summary>Receipts — signals &amp; top headlines</summary>
      <ul class="factors">{factors}</ul><ul class="heads">{heads}</ul></details>
  </div>
</article>"""

    comm_rows = ""
    for n in (community or []):
        comm_rows += (
            f'<div class="nom"><strong>{html.escape(n.get("ceo_name", ""))}</strong> '
            f'<span class="co">{html.escape(n.get("company", ""))}</span> '
            f'<span class="chip">🟡 WATCH — community signal</span><br>'
            f'<span class="sub2">{html.escape(n.get("reason", ""))}</span> '
            f'<span class="sub2">(nominated by {html.escape(n.get("submitter") or "anonymous")}, '
            f'{html.escape(n.get("added", ""))})</span></div>')
    if not comm_rows:
        comm_rows = ('<p class="sub2">No community nominations yet — the watchlist is '
                     'empty and the CEOs are getting comfortable. Fix that.</p>')
    community_html = f"""
<section class="comm" id="community">
<h2 style="margin-top:0">🗳️ Community watchlist — possible signals</h2>
<p class="sub2">Nominated by readers, stored in plain sight: every entry is a row in
<code>community.csv</code> in this repo (the database is a CSV and it is beautiful).
Nominations arrive as GitHub issues and join the CSV when approved. Allegations
are allegations — bring receipts (links) in your reason.</p>
<p><a class="nombtn" href="https://github.com/Sun-Wukhan/horniness-index/issues/new?template=nominate-ceo.yml" target="_blank" rel="noopener">➕ Nominate a CEO</a></p>
<p class="sub2">Developers: to get a CEO fully tracked — Kafka producers, dedupe, scoring —
open a pull request adding them to <code>CEOS</code> in <code>horniness-index.py</code>.
Two search queries and a dream is all it takes.</p>
{comm_rows}
</section>"""

    # Editorial pin: Jensen Huang holds #1 while his story is the live one.
    # Labeled on the card as an override — the score underneath is real.
    for _i, _r in enumerate(now_list):
        if _r["name"] == "Jensen Huang":
            now_list.insert(0, now_list.pop(_i))
            break
    now_cards = "".join(card(r, i + 1) for i, r in enumerate(now_list))
    hall_cards = "".join(card(r, i + 1, hall=True) for i, r in enumerate(hall_list))
    hall_section = ""
    if hall_list:
        hall_section = f"""
<h2 class="sect">📚 The Hall of Fame — cold cases</h2>
<p class="sub2">Historical blow-ups, scored at their peak. Nothing current — the
news cycle has moved on, the settlements have settled, the NDAs are ironclad.
Kept here so the current generation knows the bar.</p>
{hall_cards}"""
    alert = ""
    if top_live:
        verb = "is LIVE at" if top_live.get("signal") == "LIVE" else "leads the current board at"
        alert = (f'<div class="alert">🚨 <strong>MARKET ALERT:</strong> {html.escape(top_live["name"])} '
                 f'{verb} {top_live["score"]}/100 current heat. Shareholders are advised to hold — '
                 f'preferably onto something sturdy.</div>')
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Horniness Index™ — CEO Shareholder-Risk Leaderboard</title>
<meta name="description" content="A satirical, Kafka-powered leaderboard scoring CEOs on the shareholder-value risk of their relationship headlines — focused on what is happening now.">
<style>
*{{box-sizing:border-box}}
body{{background:#0d0d12;color:#f2f2f5;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:880px;margin:0 auto;padding:0 16px 64px}}
.hero{{background:linear-gradient(135deg,#2b0a1e,#4a1030 45%,#1a1033);border:1px solid #5a2342;border-radius:0 0 20px 20px;padding:34px 24px 24px;text-align:center}}
h1{{font-size:2.6rem;margin:0 0 6px;letter-spacing:-.5px}}
.tag{{color:#ffb3c6;margin:0;font-size:1.05rem}}
.alert{{background:#3d0a16;border:1px solid #ff5c8a;border-radius:12px;padding:12px 16px;margin:18px 0;font-size:.98rem}}
.stats{{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}}
.stat{{flex:1 1 140px;background:#17171f;border:1px solid #2c2c38;border-radius:12px;padding:12px;text-align:center}}
.stat b{{display:block;font-size:1.35rem;color:#ff8fab}}
.stat span{{color:#aaa;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em}}
.sect{{margin:34px 0 4px;font-size:1.5rem}} .sub2{{color:#aaa;margin:0 0 8px}}
.card{{display:flex;gap:16px;background:#17171f;border:1px solid #2c2c38;border-radius:16px;padding:18px;margin:14px 0;transition:transform .15s ease,border-color .15s ease}}
.card:hover{{transform:translateY(-2px);border-color:#ff5c8a}}
.card.top{{border:2px solid #ffb703;background:linear-gradient(180deg,#241a08,#17171f)}}
.rank{{font-size:1.9rem;font-weight:800;min-width:52px;text-align:center}}
.co{{color:#999;font-size:.85rem;font-weight:400}} .crown{{color:#ffb703;font-size:.8rem;font-weight:800;letter-spacing:.05em}}
h2{{margin:0 0 6px;font-size:1.3rem}}
.sig{{font-weight:800;margin:2px 0 8px;letter-spacing:.02em}}
.pulse{{display:inline-block;width:10px;height:10px;border-radius:50%;background:#ff2e4d;margin-right:6px;animation:pulse 1.2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.35;transform:scale(.8)}}}}
.meter{{position:relative;background:#2a2a35;border-radius:8px;height:24px;overflow:hidden}}
.bar{{height:100%;background:linear-gradient(90deg,#ffb347,#ff5c8a,#c9184a);animation:shimmer 2.4s linear infinite;background-size:200% 100%}}
@keyframes shimmer{{0%{{background-position:0% 0}}100%{{background-position:200% 0}}}}
.score{{position:absolute;right:8px;top:3px;font-weight:800;font-size:.92rem;text-shadow:0 1px 3px #000}}
.risk{{font-weight:700;margin:10px 0 4px}} .verdict{{color:#ffd6e0;font-style:italic;margin:4px 0}}
.reach{{color:#9fd8ff;font-size:.86rem;margin:4px 0}}
.chip{{display:inline-block;background:#26262f;border:1px solid #3a3a48;border-radius:999px;padding:2px 9px;font-size:.76rem;color:#ccc;margin:2px 2px 2px 0}}
details{{margin-top:8px}} summary{{cursor:pointer;color:#8ecae6}}
ul{{margin:6px 0}} a{{color:#a8d8ff}} .fine{{color:#8b8b96;font-size:.83rem;line-height:1.55;margin-top:26px}}
.badge{{display:inline-block;background:#26262f;border:1px solid #3a3a48;border-radius:999px;padding:3px 10px;font-size:.78rem;color:#ccc}}
.comm{{background:#12121a;border:1px solid #2c2c38;border-radius:16px;padding:20px;margin:30px 0}}
.nombtn{{display:inline-block;background:#c9184a;color:#fff;border-radius:8px;padding:10px 18px;font-weight:700;text-decoration:none}}
.nom{{border-top:1px solid #2c2c38;padding:10px 0}}
.feat{{width:100%;height:150px;object-fit:cover;border-radius:10px;margin-bottom:10px;background:#222}}
.thumb{{width:40px;height:40px;object-fit:cover;border-radius:6px;vertical-align:middle;margin-right:8px;background:#222}}
</style></head><body>
<header class="hero">
<h1>📈🍆 The Horniness Index™</h1>
<p class="tag">Quantifying CEO downside risk, one headline at a time.<br>
Now with a memory of about five minutes: the board scores <strong>current heat</strong> —
what is happening <em>now</em> — while the classics retire to the Hall of Fame.</p>
<p><span class="badge">🕒 Updated {generated} UTC</span> <span class="badge">🤖 Scoring: {provider}</span> <span class="badge">⚡ Powered by Kafka, hubris &amp; RSS</span></p>
<p><a class="nombtn" href="#community">🗳️ Contribute — nominate a CEO</a></p>
</header>
{alert}
<div class="stats">
  <div class="stat"><b>{len(results)}</b><span>CEOs tracked</span></div>
  <div class="stat"><b>{total_stories}</b><span>Headlines scanned</span></div>
  <div class="stat"><b>{live_count}</b><span>Live right now</span></div>
  <div class="stat"><b>{avg}/100</b><span>Average current heat</span></div>
</div>
<h2 class="sect">🔥 Happening now</h2>
<p class="sub2">Ranked by current heat: story points decay with age, so a 2019 scandal
cannot ride on vibes. Signals: 🔴 LIVE (fresh, multiple stories this week) ·
🟠 DEVELOPING (unfolding, possible escalation) · 🟡 WATCH (early smoke, possible).</p>
{now_cards}
{hall_section}
{community_html}
<p class="fine"><strong>Methodology (such as it is):</strong> four producers stream live headlines
(Google News, GDELT, outlet RSS from the BBC &amp; CNBC, and an OpenAI scout that searches the
web itself) into Kafka topic <code>headlines.raw</code>;
a dedupe consumer collapses syndicated copies into <code>headlines.clean</code> — one affair in
30 outlets is one story with a reach of 30, not 30 affairs; a scorer writes <code>ceo.scores</code>,
using OpenAI when an <code>OPENAI_API_KEY</code> secret is configured and a deterministic keyword
heuristic otherwise. Two numbers per CEO: <em>current heat</em> (recency-decayed — the main board)
and the <em>historical peak</em> (the Hall of Fame). Signals come from story dates, not vibes.
Community nominations live in <code>community.csv</code> — yes, the database is a CSV.</p>
<p class="fine"><strong>Satire &amp; sourcing:</strong> this is a meme, not investment advice and not a
factual dossier. Every score is derived only from the linked headlines; allegations in those
headlines are allegations — several are denied or unproven, and at least one board has said it has
no reason to believe any secrets were compromised. Read the sources, not just the score.</p>
<p class="fine">A loving tribute to the genre. Not affiliated with any company named.
No CEOs were consulted. One was, however, trending.</p>
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
        legacy, _lf = legacy_heuristic_score(headlines)
        if scored:
            provider = "AI-scored (OpenAI) from live headlines"
            score, factors, verdict = scored["score"], scored["factors"], scored["verdict"]
        else:
            score, factors = heat_score(headlines)
        results.append({**ceo, "headlines": headlines, "score": score,
                        "legacy_score": legacy,
                        "factors": factors, "verdict": verdict,
                        **compute_signals(headlines)})
        print(f"{ceo['name']}: {score} from {len(headlines)} headlines")
    enrich_images(results)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render(results, provider, generated, load_community(os.path.dirname(os.path.abspath(__file__)))))
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": generated, "provider": provider, "results": results, "community": load_community(os.path.dirname(os.path.abspath(__file__)))}, f, indent=2)
    print(f"Wrote {out_dir}/index.html and data.json ({provider})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

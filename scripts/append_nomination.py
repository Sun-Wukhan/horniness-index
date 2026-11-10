#!/usr/bin/env python3
"""Append an approved nomination issue to community.csv.

Run by .github/workflows/nominations.yml when a `nomination` issue gets the
`approved` label. Inputs via environment:
  ISSUE_BODY   — the issue body (GitHub Issue Forms render "### <label>")
  ISSUE_AUTHOR — the issue author's login (recorded as the submitter)
  CSV_PATH     — optional override for tests (default: community.csv)

Exit 0 with "appended" / "duplicate" / "invalid: ..." on stdout; the
workflow comments the outcome back on the issue. Never raises for bad
input — a malformed nomination is a comment, not a failed run.
"""

import csv
import os
import re
import sys
from datetime import date

FIELDS = {"ceo name": "ceo_name", "company": "company",
          "why they belong on the index": "reason",
          "receipts (links)": "receipts"}


def parse_form(body):
    out, current = {}, None
    for line in (body or "").splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            current = FIELDS.get(m.group(1).strip().lower())
            if current:
                out[current] = ""
            continue
        if current:
            out[current] += line + "\n"
    return {k: re.sub(r"\s+", " ", v).strip() for k, v in out.items()
            if v.strip() and v.strip() != "_No response_"}


def clean(text, limit):
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


def main():
    data = parse_form(os.environ.get("ISSUE_BODY", ""))
    name = clean(data.get("ceo_name"), 80)
    reason = clean(data.get("reason"), 500)
    if len(name) < 2 or len(reason) < 10:
        print("invalid: need a CEO name and a real reason (>= 10 chars)")
        return 0
    receipts = clean(data.get("receipts"), 200)
    if receipts:
        reason = f"{reason} Receipts: {receipts}"[:500]
    path = os.environ.get("CSV_PATH", "community.csv")
    rows = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    if any((r.get("ceo_name") or "").strip().lower() == name.lower() for r in rows):
        print(f"duplicate: {name} is already on the watchlist")
        return 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([name, clean(data.get("company"), 80), reason,
                    os.environ.get("ISSUE_AUTHOR", ""), date.today().isoformat()])
    print(f"appended: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

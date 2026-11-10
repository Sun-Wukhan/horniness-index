#!/usr/bin/env python3
"""Horniness Index — submissions backend.

Lets ANYONE post to the site's Contribute form without a GitHub account,
while nothing reaches the site without the maintainer's approval.

How it works:
  * POST /submit (public) validates the form and files it as a GitHub
    issue in Sun-Wukhan/horniness-index (labels: nomination, web) using a
    fine-grained PAT scoped to that one repo, Issues read/write only.
    The issue body uses the exact ### format the nomination bot parses.
  * /admin (password login) lists pending web submissions; Approve adds
    the `approved` label — the existing nominations workflow then appends
    the row to community.csv and it renders on the next build. Reject
    closes the issue with a `rejected` label.
  * GitHub Issues are the database: durable, auditable, zero extra infra.

Env: GITHUB_TOKEN, ADMIN_PASSWORD, ADMIN_SECRET (cookie HMAC key),
     SITE_ORIGIN (CORS, default https://sun-wukhan.github.io)
"""

import hashlib
import hmac
import html
import json
import os
import re
import time
import urllib.request
from collections import defaultdict, deque

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

REPO = "Sun-Wukhan/horniness-index"
GH = "https://api.github.com"
SITE_ORIGIN = os.environ.get("SITE_ORIGIN", "https://sun-wukhan.github.io")
COOKIE = "hi_admin"

app = FastAPI(title="horniness-index submissions")


# ---------------------------------------------------------------- GitHub
def gh(method, path, payload=None):
    req = urllib.request.Request(
        GH + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
            "accept": "application/vnd.github+json",
            "content-type": "application/json",
            "user-agent": "horniness-index-backend",
        })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp) if resp.length != 0 else {}


def file_issue(name, company, reason, receipts):
    body = (f"### CEO name\n{name}\n\n### Company\n{company}\n\n"
            f"### Why they belong on the Index\n{reason}\n\n"
            f"### Receipts (links)\n{receipts}\n\n"
            f"— submitted from the website (no GitHub account needed)")
    return gh("POST", f"/repos/{REPO}/issues",
              {"title": f"Nominate: {name}", "body": body,
               "labels": ["nomination", "web"]})


def list_pending():
    issues = gh("GET", f"/repos/{REPO}/issues?state=open&labels=web"
                       f"&per_page=50&sort=created&direction=asc")
    out = []
    for i in issues:
        if "pull_request" in i:
            continue
        labels = {l["name"] for l in i.get("labels", [])}
        out.append({"number": i["number"], "title": i["title"],
                    "body": i.get("body") or "",
                    "created": i["created_at"][:10],
                    "approved": "approved" in labels,
                    "url": i["html_url"]})
    return out


# ------------------------------------------------------------------ auth
def sign(value):
    key = os.environ["ADMIN_SECRET"].encode()
    return value + "." + hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def authed(request):
    raw = request.cookies.get(COOKIE, "")
    if "." not in raw:
        return False
    value, _sig = raw.rsplit(".", 1)
    return hmac.compare_digest(sign(value), raw) and value.startswith("ok:")


# -------------------------------------------------------------- limiting
_hits = defaultdict(deque)


def rate_ok(ip, limit=5, window=3600):
    now = time.time()
    q = _hits[ip]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def clean(text, limit):
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


# ---------------------------------------------------------------- routes
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/submit")
def submit(request: Request, ceo_name: str = Form(""),
           company: str = Form(""), reason: str = Form(""),
           receipts: str = Form(""), website: str = Form("")):
    # NOTE: a plain def, not async — the GitHub call below is blocking
    # I/O; FastAPI runs sync endpoints in a threadpool. An async version
    # froze the whole event loop (and every other request) for the
    # duration of the GitHub call. Diagnosed live on day one.
    if website:  # honeypot — humans never see this field
        return JSONResponse({"ok": True})
    ip = request.client.host if request.client else "?"
    if not rate_ok(ip):
        return JSONResponse({"ok": False, "error": "Slow down — too many "
                             "submissions from this connection. Try later."},
                            status_code=429)
    name = clean(ceo_name, 80)
    company = clean(company, 80)
    reason = clean(reason, 500)
    receipts = clean(receipts, 300)
    if len(name) < 2 or len(reason) < 10:
        return JSONResponse({"ok": False, "error": "Needs a CEO name and a "
                             "real update (10+ characters)."}, status_code=400)
    try:
        issue = file_issue(name, company, reason, receipts)
    except Exception:
        return JSONResponse({"ok": False, "error": "The Index's filing "
                             "clerk is on a break — try again in a bit."},
                            status_code=502)
    resp = JSONResponse({"ok": True, "number": issue.get("number")})
    resp.headers["Access-Control-Allow-Origin"] = SITE_ORIGIN
    return resp


@app.options("/submit")
def submit_options():
    resp = JSONResponse({"ok": True})
    resp.headers["Access-Control-Allow-Origin"] = SITE_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "POST"
    resp.headers["Access-Control-Allow-Headers"] = "content-type"
    return resp


LOGIN = """<!DOCTYPE html><html><body style="font-family:system-ui;background:#0d0d12;color:#f2f2f5;max-width:420px;margin:60px auto;padding:0 16px">
<h2>📈 Horniness Index — admin</h2>
<form method="post" action="/admin/login">
<input type="password" name="password" placeholder="Admin password" style="width:100%;padding:10px;border-radius:8px;border:1px solid #3a3a48;background:#17171f;color:#fff">
<button style="margin-top:10px;padding:10px 18px;border-radius:8px;background:#c9184a;color:#fff;border:0;font-weight:700">Log in</button>
</form></body></html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    if not authed(request):
        return HTMLResponse(LOGIN)
    rows = []
    for s in list_pending():
        body = html.escape(s["body"]).replace("\n", "<br>")
        state = "✅ approved (queued for community.csv)" if s["approved"] else \
            f"""<form method="post" action="/admin/approve" style="display:inline">
<input type="hidden" name="number" value="{s['number']}">
<button style="padding:6px 14px;border-radius:6px;background:#2a9d4a;color:#fff;border:0;font-weight:700">Approve</button></form>
<form method="post" action="/admin/reject" style="display:inline">
<input type="hidden" name="number" value="{s['number']}">
<button style="padding:6px 14px;border-radius:6px;background:#555;color:#fff;border:0">Reject</button></form>"""
        rows.append(f"""<div style="border:1px solid #2c2c38;border-radius:12px;padding:14px;margin:12px 0">
<div><strong>#{s['number']} {html.escape(s['title'])}</strong> <span style="color:#999">{s['created']}</span>
· <a href="{s['url']}" style="color:#a8d8ff">on GitHub</a></div>
<div style="color:#ccc;margin:8px 0;font-size:.92rem">{body}</div>{state}</div>""")
    listing = "".join(rows) or "<p style='color:#aaa'>No pending web submissions. The CEOs rest easy.</p>"
    return HTMLResponse(f"""<!DOCTYPE html><html><body style="font-family:system-ui;background:#0d0d12;color:#f2f2f5;max-width:760px;margin:30px auto;padding:0 16px">
<h2>📈 Horniness Index — pending web submissions</h2>
<p style="color:#aaa">Approving adds the <code>approved</code> label; the nomination bot then files it into community.csv and it renders on the next build.</p>
{listing}
<form method="post" action="/admin/logout"><button style="background:none;border:0;color:#8ecae6;cursor:pointer">Log out</button></form>
</body></html>""")


@app.post("/admin/login")
def login(password: str = Form(...)):
    if not hmac.compare_digest(password, os.environ.get("ADMIN_PASSWORD", "")):
        return HTMLResponse(LOGIN + "<p style='color:#ff5c8a'>Wrong password.</p>",
                            status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(COOKIE, sign(f"ok:{int(time.time())}"),
                    max_age=7 * 24 * 3600, httponly=True, samesite="lax",
                    secure=True)
    return resp


@app.post("/admin/logout")
def logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.post("/admin/approve")
def approve(request: Request, number: int = Form(...)):
    if not authed(request):
        return RedirectResponse("/admin", status_code=303)
    gh("POST", f"/repos/{REPO}/issues/{number}/labels",
       {"labels": ["approved"]})
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/reject")
def reject(request: Request, number: int = Form(...)):
    if not authed(request):
        return RedirectResponse("/admin", status_code=303)
    gh("POST", f"/repos/{REPO}/issues/{number}/labels",
       {"labels": ["rejected"]})
    gh("PATCH", f"/repos/{REPO}/issues/{number}", {"state": "closed"})
    return RedirectResponse("/admin", status_code=303)

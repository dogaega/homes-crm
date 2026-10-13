"""
Log replies — never answer them (Mark replies himself on WhatsApp). Pulls the
latest WhatsApp history into the CRM, then reads each new reply to an outreach
message and records per property: available / rented / under offer, whether
the agency holds the mandate exclusively, or what they need (tenant file …),
with the date they said it.

    python -m outreach.replies
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach.llm import chat_json  # noqa: E402
from sync import netwait  # noqa: E402,F401
from sync.worker_client import WorkerClient  # noqa: E402

READ = """An agent replied to Mark's enquiry. Mark asked about these properties (id: description):
{props}
Mark's message: {out}
Agent's reply: {reply}
For each property the reply is about, return its status. Return ONLY JSON:
{{"options": [{{"property_id": "...", "status": "available | unavailable | rented | under_offer | sold | unknown" (unavailable = no longer available, reason not given),
   "mandate": "exclusive | not_exclusive | null", "note": "max 12 words, e.g. needs tenant file / visit Tuesday / price now 1.6M"}}],
  "summary": "max 15 words"}}
Only use what the reply says; if it says nothing about a property leave it out."""


def email_replies(w: WorkerClient, days: int = 3) -> int:
    """Pull replies to outreach emails from markmirimsky@gmail.com (IMAP, same app password) into the CRM.
    Only mail from a domain we emailed is taken; auto-replies (out of office …) are skipped."""
    import email
    import imaplib
    import os
    import re
    from email.header import decode_header, make_header
    from email.utils import parseaddr, parsedate_to_datetime
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    if not pw:
        return 0
    sent = [m for m in w.request("GET", "/sync/outreach/queue?status=sent")["messages"] if m["channel"] == "email"]
    by_domain = {m["to_address"].split("@")[-1].lower(): m["to_address"].lower() for m in sent if "@" in (m["to_address"] or "")}
    if not by_domain:
        return 0
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login("markmirimsky@gmail.com", pw)
    M.select("INBOX", readonly=True)
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    n = 0
    for i in M.search(None, "SINCE", since)[1][0].split():
        msg = email.message_from_bytes(M.fetch(i, "(BODY.PEEK[])")[1][0][1])
        name, addr = parseaddr(msg.get("From") or "")
        addr = addr.lower()
        dom = addr.split("@")[-1]
        subj = str(make_header(decode_header(msg.get("Subject") or "")))
        if dom not in by_domain or msg.get("Auto-Submitted", "no") != "no" or re.search(r"absence|out of office|automatic reply|réponse automatique|auto", subj, re.I):
            continue
        body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                break
        body = re.split(r"\n(?:On .+ wrote:|Le .+ a écrit\s*:|-----Original)", body)[0].strip()  # drop the quoted outreach
        at = parsedate_to_datetime(msg["Date"]).astimezone(dt.timezone.utc).isoformat()
        key = by_domain[dom]  # the address we wrote to (a colleague answering is logged on that thread)
        r = w.request("POST", "/sync/outreach/ingest", {
            "contacts": [{"key": key, "email": key, "agency_website": dom, "source": "email"}],
            "interactions": [{"contact_key": key, "channel": "email", "direction": "in", "account": "markmirimsky@gmail.com",
                              "external_id": "mail:" + (msg.get("Message-ID") or f"{addr}-{at}"), "peer": addr, "at": at,
                              "text": (f"[{name or addr}] " if addr != key else "") + subj + "\n" + body[:4000]}]})
        n += r.get("interactions", 0)
    M.logout()
    return n


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    since = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    # 1. fresh WhatsApp history (both accounts) into the CRM
    subprocess.run([sys.executable, "-m", "outreach.whatsapp_history", "--since", since], cwd=root, check=False,
                   stdout=subprocess.DEVNULL)
    w = WorkerClient(timeout=120)
    print(f"{email_replies(w)} new email replies", flush=True)
    replies = w.request("GET", f"/sync/outreach/inbox?since={since}")["replies"]
    # Group the burst of messages one person sends into one reply.
    by_contact: dict[str, list] = {}
    for r in replies:
        by_contact.setdefault(r["contact_id"], []).append(r)
    for cid, rs in by_contact.items():
        out = rs[-1]["last_out"]
        pids, reqs = json.loads(out["property_ids"] or "[]"), json.loads(out["request_ids"] or "[]")
        text = "\n".join(r["text"] or "" for r in rs)
        info = w.request("POST", "/sync/outreach/props", {"ids": pids})["props"]
        desc = "\n".join(f"{p['id']}: {p.get('title') or ''} — {p.get('city') or ''}, {p.get('living_area_sqm') or '?'} m², "
                         f"€{p.get('price') or '?'}; links: {(p.get('urls') or '')[:300]}" for p in info)
        res = chat_json(READ.format(props=desc, out=out["body"][:1500], reply=text[:3000])) or {}
        opts = [{"request_id": rq, "property_id": o["property_id"], "status": o.get("status"), "mandate": o.get("mandate"),
                 "note": o.get("note"), "at": rs[-1]["at"]}
                for o in res.get("options", []) if o.get("property_id") in pids and o.get("status") not in (None, "unknown") for rq in reqs]
        for i, r in enumerate(rs):
            w.request("POST", "/sync/outreach/classified", {"interaction_id": r["id"], "classification": res if i == len(rs) - 1 else {"part_of": rs[-1]["id"]},
                                                           "options": opts if i == len(rs) - 1 else []})
        print(f"{rs[-1]['agency']} / {rs[-1]['person']}: {res.get('summary')}", flush=True)
    print(f"{len(by_contact)} replies logged")


if __name__ == "__main__":
    main()

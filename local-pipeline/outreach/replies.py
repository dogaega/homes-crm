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
{{"options": [{{"property_id": "...", "status": "available | rented | under_offer | sold | unknown",
   "mandate": "exclusive | not_exclusive | null", "note": "max 12 words, e.g. needs tenant file / visit Tuesday / price now 1.6M"}}],
  "summary": "max 15 words"}}
Only use what the reply says; if it says nothing about a property leave it out."""


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    since = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    # 1. fresh WhatsApp history (both accounts) into the CRM
    subprocess.run([sys.executable, "-m", "outreach.whatsapp_history", "--since", since], cwd=root, check=False,
                   stdout=subprocess.DEVNULL)
    w = WorkerClient(timeout=120)
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

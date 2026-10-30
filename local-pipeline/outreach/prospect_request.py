"""
Write to local agencies we have never contacted about ONE urgent request, even
when we could not scrape their listings (rental / management agencies, parked
sites): their office email from their own website, the request first, ask for
what they have directly with the owner. Emails are queued "approved" and go out
through outreach.send like the others (one every few minutes).

    python -m outreach.prospect_request --request extra-beausoleil-rent-2500 --towns "BEAUSOLEIL|CAP.D.AIL|ROQUEBRUNE|LA TURBIE" [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach.contact_finder import read  # noqa: E402
from outreach.daily import PRIOR, _dom, clean_email, emailed_domains  # noqa: E402
from sync.worker_client import WorkerClient  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
BODIES = {
    "extra-beausoleil-rent-2500": (
        "Hello, I have a client looking to rent urgently:\nBEAUSOLEIL – RENT\n• apartment, studio or 1 bedroom, up to €2,500/month\n\n"
        "Do you have anything available directly with the owner? Could you send me the brochures please?\n\n"
        "Please reply by email. Thank you\nMark",
        "Apartment to rent in Beausoleil"),
}


def candidates(towns: str) -> list[dict]:
    disc: dict = {}
    for f in sorted(DATA.glob("discovered_*.json")):
        disc.update(json.loads(f.read_text()))
    cfg = {c.get("agency"): c for c in json.loads((DATA / "site_configs.json").read_text())}
    out, seen = [], set()
    for v in disc.values():
        site = v.get("website") or (cfg.get(v.get("name")) or {}).get("website")
        if not site or not re.search(towns, v.get("commune") or "", re.I):
            continue
        h = _dom(site)
        if h in seen or re.search(r"facebook|instagram|google|pagesjaunes|linkedin", h):
            continue
        seen.add(h)
        out.append({"name": v.get("name"), "site": site, "email": clean_email(v.get("email"))})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--towns", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--exclude", default="", help="regex of agency names / emails to leave out (wrong websites)")
    args = ap.parse_args()
    body, subject = BODIES[args.request]
    done = emailed_domains() | set(PRIOR["domains"])
    w = WorkerClient(timeout=120)
    msgs = []
    for c in candidates(args.towns):
        email = c["email"]
        if not email:
            for page in (c["site"], c["site"].rstrip("/") + "/contact"):
                try:
                    found = [e for e in (read(page).get("emails") or []) if clean_email(e) and not re.search(r"donnees|rgpd|privacy|dpo", e)]
                except Exception:  # noqa: BLE001
                    found = []
                own = [e for e in found if _dom(e) == _dom(c["site"])]
                email = clean_email((own or found or [None])[0])
                if email:
                    break
        if args.exclude and re.search(args.exclude, f"{c['name']} {email or ''}", re.I):
            print(f"skip {c['name']}: excluded (not a real-estate address)")
            continue
        if not email or _dom(email) in done:
            print(f"skip {c['name']}: {'no email' if not email else 'already emailed'}")
            continue
        done.add(_dom(email))
        msgs.append({"agency_id": None, "channel": "email", "account": "markmirimsky@gmail.com", "to_address": email, "subject": subject,
                     "body": body, "status": "approved", "purpose": "first_contact", "request_ids": [args.request], "property_ids": [],
                     "person_name": None, "reason": f"{c['name']} — local agency never contacted, urgent request {args.request} (Mark)"})
        print(f"=== {c['name']} → {email}")
    if msgs and not args.dry_run:
        print(w.request("POST", "/sync/outreach/queue", {"messages": msgs}))
    print(f"{len(msgs)} emails")


if __name__ == "__main__":
    main()

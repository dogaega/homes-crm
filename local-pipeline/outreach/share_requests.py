"""
Share current client requests with agents Mark already talks to, so they send
what they have directly with the owner. Per agent: at most 2 requests, only
the rent/sale type and towns their agency actually works in, hottest clients
first. Pacing: nobody written to in the last 7 days, and nobody whose last
message from us is still unanswered. Each message is worded differently.

Drafts are queued with status "draft" (nothing is sent) until Mark says which
WhatsApp number to send from.

    python -m outreach.share_requests [--dry-run] [--limit 15]
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach.daily import priority  # noqa: E402
from outreach.llm import chat_json  # noqa: E402
from outreach.request_groups import for_specialty, group_lines, team_requests  # noqa: E402
from sync import netwait  # noqa: E402,F401
from sync.worker_client import WorkerClient  # noqa: E402

MSG = """Write ONE short WhatsApp message from Mark to {name}, a real-estate agent he already knows ({agency}).
Goal: share his clients' current requests and ask them to send brochures ONLY of properties they have directly with the owner
(exclusive / own mandate) that could fit. Requests — copy this block exactly as it is, headings and bullets, no client names:
{requests}
Mark's style (plain, short, casual phone English; his real messages): "Hello, do you have anything for rent in monaco at 6000?",
"Could you send the brochures please? So I could show to my client", "do you have this one straight from the owner?".
Rules: 2-4 short sentences, greet by first name if given, no em dashes, no semicolons, no emojis, no fancy words, never mention viewings,
dates or commission, never invent facts. Do NOT reuse wording from: {avoid}
Return ONLY JSON: {{"message": "..."}}"""


def first(name: str | None) -> str:
    n = (name or "").strip()
    return n.split()[0] if n and not re.search(r"\d|agence|agency|immo|real|estate|unknown", n, re.I) else ""


def req_text(c: dict) -> str:
    beds = c.get("bedrooms_min")
    kind = {"villa": "villa", "apartment": "apartment"}.get(c.get("kind"), "apartment or house")
    money = f"€{int(c['price_max']):,}" + ("/month" if c.get("transaction_type") == "rent" else "")
    return (f"- {'for rent' if c.get('transaction_type') == 'rent' else 'to buy'}: {kind} in {' / '.join(c.get('cities') or [])}"
            + (f", {beds}+ bedrooms" if beds else "") + f", up to {money}")


INTROS = ["Hello{n}, these are the requests I have from my clients at the moment:",
          "Hi{n}, I have a few client requests that could fit what you work with:",
          "Hello{n}, sharing my current client requests in your area:",
          "Hi{n}, at the moment my clients are looking for:"]
OUTRO = "If you have anything directly with the owner that fits, could you send me the brochures please?\nThank you\nMark"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", action="store_true", help="also queue the drafts (status draft) in the CRM")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "outreach" / "requests_per_agent.txt"))
    args = ap.parse_args()
    w = WorkerClient(timeout=180)
    reqs = team_requests()
    now = dt.datetime.now(dt.timezone.utc)
    age = lambda x: (now - dt.datetime.fromisoformat(x.replace("Z", "+00:00"))).days if x else 999
    by: dict = {}
    for a in w.request("GET", "/sync/outreach/known-agents")["agents"]:
        g = by.setdefault(a["agency"], {"areas": a.get("areas") or [], "people": []})
        g["people"].append(a)
    blocks, drafts = [], []
    for i, (agency, g) in enumerate(sorted(by.items(), key=lambda kv: kv[0].lower())):
        chosen = for_specialty(reqs, g["areas"])
        if not chosen:
            continue
        # write to the person we talk to most, if not written to in the last 7 days and not left unanswered
        people = sorted(g["people"], key=lambda p: -(p.get("msgs") or 0))
        free = [p for p in people if not (age(p.get("last_out")) < 7 or (p.get("last_out") and (p.get("last_in") or "") < p["last_out"]))]
        to = (free or people)[0]
        nm = first(to.get("name"))
        msg = INTROS[i % len(INTROS)].format(n=f" {nm}" if nm else "") + "\n\n" + "\n".join(group_lines(chosen)) + "\n\n" + OUTRO
        contact = " · ".join(x for x in [to.get("name"), to.get("whatsapp") or to.get("phone"), to.get("email")] if x)
        spec = "; ".join(f"{x['city']} {x['tx']} ({x['n']})" for x in sorted(g["areas"], key=lambda x: -x["n"])[:4])
        blocks.append(f"===== {agency} — {contact}\nSpecialty: {spec}\nFor (internal, never send): "
                      + ", ".join(f"{r['id']} №{r['no']}" for r in chosen) + ("" if free else "\n(note: last message to them is unanswered or recent — wait)")
                      + f"\n\n{msg}\n")
        drafts.append({"agency_id": to["agency_id"], "contact_id": to["id"], "channel": "whatsapp", "account": None,
                       "to_address": to.get("whatsapp") or to.get("phone") or to.get("email"), "person_name": to.get("name"), "body": msg,
                       "status": "draft", "purpose": "share_requests", "request_ids": [], "property_ids": [],
                       "reason": "requests matching their specialty: " + ", ".join(r["id"] for r in chosen)})
    Path(args.out).write_text("\n".join(blocks))
    if drafts and args.queue:
        print(w.request("POST", "/sync/outreach/queue", {"messages": drafts}))
    print(f"{len(drafts)} agencies → {args.out}")


if __name__ == "__main__":
    main()

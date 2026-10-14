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
from sync import netwait  # noqa: E402,F401
from sync.worker_client import WorkerClient  # noqa: E402

MSG = """Write ONE short WhatsApp message from Mark to {name}, a real-estate agent he already knows ({agency}).
Goal: share his clients' current requests and ask them to send brochures ONLY of properties they have directly with the owner
(exclusive / own mandate) that could fit. Requests (share exactly these figures, no client names):
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=15, help="agents per run")
    args = ap.parse_args()
    w = WorkerClient(timeout=180)
    reqs = [r for r in w.request("GET", "/sync/outreach/requests")["requests"] if r["c"].get("sheet_no")]
    reqs = [(priority(r)[0], r) for r in reqs]
    reqs = [(s, r) for s, r in reqs if s >= 0]
    now = dt.datetime.now(dt.timezone.utc)
    age = lambda x: (now - dt.datetime.fromisoformat(x.replace("Z", "+00:00"))).days if x else 999
    drafts, avoid = [], []
    agents = w.request("GET", "/sync/outreach/known-agents")["agents"]
    for a in sorted(agents, key=lambda a: -(a.get("msgs") or 0)):
        phone = a.get("whatsapp") or a.get("phone")
        if not phone or not a.get("areas"):
            continue
        if age(a.get("last_out")) < 7 or (a.get("last_out") and (a.get("last_in") or "") < a["last_out"]):
            continue  # pacing: written recently, or our last message is unanswered
        works = {(x["city"].lower(), x["tx"]) for x in a["areas"] if x["n"] >= 1}
        fit = [(s, r) for s, r in reqs if any(((t.lower(), r["c"].get("transaction_type") or "sale") in works) for t in r["c"].get("cities") or [])]
        if not fit:
            continue
        fit.sort(key=lambda sr: -sr[0])
        # one type only per message: the hottest request decides rent vs sale and the town
        tx0 = fit[0][1]["c"].get("transaction_type")
        town0 = {t.lower() for t in fit[0][1]["c"].get("cities") or []}
        chosen = [r for s, r in fit if r["c"].get("transaction_type") == tx0 and town0 & {t.lower() for t in r["c"].get("cities") or []}][:2]
        text = "\n".join(req_text(r["c"]) for r in chosen)
        out = chat_json(MSG.format(name=first(a.get("name")) or "the agent", agency=a.get("agency"), requests=text,
                                   avoid=avoid[-6:]), temperature=0.8) or {}
        msg = out.get("message") or ""
        if not msg or re.search(r"[—–;]|viewing|visit|commission", msg, re.I):
            continue
        avoid.append(msg[:120])
        drafts.append({"agency_id": a["agency_id"], "contact_id": a["id"], "channel": "whatsapp", "account": None, "to_address": phone,
                       "person_name": a.get("name"), "body": msg, "status": "draft", "purpose": "share_requests",
                       "request_ids": [r["id"] for r in chosen], "property_ids": [],
                       "reason": f"known agent ({a.get('msgs')} messages); shares " + ", ".join(f"№{r['c'].get('sheet_no')}" for r in chosen)})
        print(f"\n=== {a.get('name')} / {a.get('agency')} ({phone})\n{msg}", flush=True)
        if len(drafts) >= args.limit:
            break
    if drafts and not args.dry_run:
        print(w.request("POST", "/sync/outreach/queue", {"messages": drafts}))
    print(f"\n{len(drafts)} drafts")


if __name__ == "__main__":
    main()

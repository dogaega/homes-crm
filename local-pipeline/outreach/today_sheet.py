"""
"Today" tab for Mark's Google Sheet: priority client requests (Artem's app +
sheet), what went out for each, and which agents are waiting for an answer.
Output is a TSV (paste at A1 of the "Today" tab). Internal: client names stay
in Mark's own sheet only. Nothing is sent.

    python -m outreach.today_sheet --out FILE
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach.daily import priority  # noqa: E402
from sync.worker_client import WorkerClient  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    w = WorkerClient(timeout=180)
    now = dt.datetime.now(dt.timezone.utc)
    reqs = [r for r in w.request("GET", "/sync/outreach/requests")["requests"] if r["c"].get("sheet_no")]
    sent = [m for m in w.request("GET", "/sync/outreach/queue?status=sent")["messages"] if m.get("channel") == "email"]
    by_req: dict[str, list] = {}
    for m in sent:
        for rid in json.loads(m.get("request_ids") or "[]"):
            by_req.setdefault(rid, []).append(m)
    rows = []
    for r in reqs:
        score, why = priority(r)
        if score < 0:
            continue
        ms = by_req.get(r["id"], [])
        day = [m for m in ms if (now - dt.datetime.fromisoformat(m["sent_at"].replace("Z", "+00:00"))).days < 1]
        c = r["c"]
        what = f"{'Rent' if c.get('transaction_type') == 'rent' else 'Buy'} · {' / '.join(c.get('cities') or [])}" \
               + (f" · {c['bedrooms_min']}+ bed" if c.get("bedrooms_min") else "") \
               + (f" · up to €{int(c['price_max']):,}" if c.get("price_max") else " · no budget")
        rows.append([score, f"№{c.get('sheet_no')}", r["name"], what, why, len(day), len(ms),
                     ", ".join(sorted({m.get("agency") or "" for m in day}))[:300]])
    rows.sort(key=lambda x: -x[0])
    waiting = []
    for a in w.request("GET", "/sync/outreach/known-agents")["agents"]:
        if a.get("last_in") and (not a.get("last_out") or a["last_in"] > a["last_out"]):
            waiting.append([a.get("agency"), a.get("name") or "", a.get("whatsapp") or a.get("phone") or a.get("email") or "", a["last_in"][:10]])
    waiting.sort(key=lambda x: x[3], reverse=True)
    cell = lambda v: (lambda t: f'"{t}"' if any(ch in t for ch in '\n\t"') else t)(str(v).replace('"', '""'))
    out = [[f"Updated {dt.datetime.now().strftime('%d.%m %H:%M')}"], [],
           ["Priority", "№", "Client (internal)", "Request", "Why priority", "Emails last 24h", "Emails total", "Agencies emailed last 24h"]]
    out += [[str(x) for x in r] for r in rows]
    out += [[], ["Agents who wrote last — answer them", "", "", ""], ["Agency", "Agent", "Contact", "Last message from them"]] + waiting[:40]
    Path(args.out).write_text("\n".join("\t".join(cell(v) for v in r) for r in out))
    print(f"{len(rows)} priority requests, {len(waiting)} agents waiting for an answer")


if __name__ == "__main__":
    main()

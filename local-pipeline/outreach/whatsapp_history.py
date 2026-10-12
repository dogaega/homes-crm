"""
Import Mark's WhatsApp history with French / Monaco real-estate contacts
(Baileys store, accounts "mark" and "third") into the CRM: one agency person
per contact (agency, name, role, how Mark presented himself) and every
message as an interaction. Re-runnable: messages are stored once.

    python -m outreach.whatsapp_history [--since 2026-05-01]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach.llm import chat_json  # noqa: E402
from sync import netwait  # noqa: E402,F401
from sync.worker_client import WorkerClient  # noqa: E402

STORE = Path.home() / "Projects/bazaraki-listings/_tools/whatsapp_service/auth_state"
ACCOUNTS = ("mark", "third")
CACHE = Path(__file__).resolve().parents[1] / "data" / "outreach_contacts_cache.json"

IDENTIFY = """You look at a WhatsApp conversation between Mark (a real-estate broker; "ME") and a contact.
Decide who the contact is. Return ONLY JSON:
{"real_estate": true/false (is the contact a real-estate agent / agency / owner offering property in France or Monaco?),
 "agency_name": "company name or null", "agency_website": "domain or null", "person_name": "first + last name or null",
 "role": "agent | manager | rentals | owner | developer | other | null",
 "persona": "agent if Mark told them he is an agency / broker / has a client, private if he wrote as a private person, else unknown",
 "language": "en | fr | ru | it | other"}
Contact saved as: {name}  Phone: {phone}
Conversation (oldest first, truncated):
{conv}"""


def ts(m: dict) -> float:
    t = m.get("timestamp") or 0
    return t / 1000 if t > 1e11 else t


def phone_of(jid: str, msgs: list[dict]) -> str | None:
    if jid.endswith("@s.whatsapp.net"):
        return jid.split("@")[0]
    for m in msgs:
        alt = (m.get("key") or {}).get("remoteJidAlt") or ""
        if alt.endswith("@s.whatsapp.net"):
            return alt.split("@")[0]
    return None


def text_of(m: dict) -> str:
    t = (m.get("text") or "").strip()
    if t and t != "[non-text message]":
        return t
    return {"documentMessage": "[document]", "documentWithCaptionMessage": "[document]", "imageMessage": "[photo]",
            "videoMessage": "[video]", "audioMessage": "[voice note]"}.get(m.get("msgType") or "", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-03-01")
    args = ap.parse_args()
    since = dt.datetime.fromisoformat(args.since).timestamp()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    client = WorkerClient(timeout=180)
    stats = {"contacts": 0, "skipped_not_real_estate": 0, "messages": 0}
    for acc in ACCOUNTS:
        store = json.loads((STORE / acc / "chats.json").read_text())
        for jid, chat in store["chats"]:
            if "@g.us" in jid or "broadcast" in jid or "newsletter" in jid:
                continue
            msgs = [m for m in chat.get("messages", []) if ts(m) >= since and text_of(m)]
            phone = phone_of(jid, chat.get("messages", []))
            if not msgs or not phone or not (phone.startswith("33") or phone.startswith("377")):
                continue
            key = f"{acc}:{phone}"
            if key not in cache:
                conv = "\n".join(f"{'ME' if m.get('fromMe') else 'THEM'}: {text_of(m)[:300]}" for m in msgs[:40])
                cache[key] = chat_json(IDENTIFY.replace("{name}", chat.get("name") or jid).replace("{phone}", "+" + phone)
                                       .replace("{conv}", conv[:7000])) or {}
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            who = cache[key]
            if not who.get("real_estate"):
                stats["skipped_not_real_estate"] += 1
                continue
            # The CRM matches the agency by phone first; the name is the fallback (kept even when unknown,
            # so the history is not lost and can be merged later).
            contact = {"key": key, "agency_name": who.get("agency_name") or f"Unknown (+{phone})",
                       "agency_website": who.get("agency_website"), "name": who.get("person_name") or (chat.get("name") if "@" not in (chat.get("name") or "@") else None),
                       "phone": "+" + phone, "whatsapp": "+" + phone, "whatsapp_jid": jid, "role": who.get("role"),
                       "source": f"whatsapp:{acc}", "persona": who.get("persona") if who.get("persona") in ("agent", "private") else None}
            inter = [{"contact_key": key, "channel": "whatsapp", "direction": "out" if m.get("fromMe") else "in", "account": acc,
                      "external_id": f"wa:{acc}:{(m.get('key') or {}).get('id')}", "thread_ref": jid, "peer": "+" + phone,
                      "at": dt.datetime.fromtimestamp(ts(m), dt.timezone.utc).isoformat(), "text": text_of(m)[:4000]} for m in msgs]
            for i in range(0, len(inter), 200):
                r = client.request("POST", "/sync/outreach/ingest", {"contacts": [contact], "interactions": inter[i:i + 200]})
                stats["messages"] += r.get("interactions", 0)
            stats["contacts"] += 1
            print(f"{acc} +{phone} {contact['name']!s:28} {contact['agency_name']!s:34} persona={contact['persona']} msgs={len(msgs)}", flush=True)
    print(stats)


if __name__ == "__main__":
    main()

"""
Send queued outreach messages, paced so Mark's WhatsApp is never flagged:
WhatsApp only Mon–Sat 09:00–19:00 Paris time, one message per run, at least a
random 4–9 minutes after the previous one (via the local Baileys service,
account "mark", which shows "typing…" first); email from markmirimsky@gmail.com
over Gmail SMTP with an app password (GMAIL_APP_PASSWORD in .env, created by
Mark), 1–3 minutes apart. Every send is logged in the CRM. Nothing here ever
replies to anyone. Run every few minutes (launchd).

    python -m outreach.send [--once]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sync import netwait  # noqa: E402,F401
from sync.worker_client import WorkerClient  # noqa: E402
from outreach.form_filler import submit as submit_form  # noqa: E402

BAILEYS = "http://localhost:8850"
STATE = Path(__file__).resolve().parents[1] / "data" / "outreach_send_state.json"
PARIS = ZoneInfo("Europe/Paris")
GMAIL = os.environ.get("GMAIL_ADDRESS", "markmirimsky@gmail.com")


def in_hours(now: dt.datetime) -> bool:
    t = now.astimezone(PARIS)
    return t.weekday() < 6 and 9 <= t.hour < 19


def state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=1))


def send_whatsapp(phone: str, text: str) -> tuple[bool, str | None, str | None]:
    digits = re.sub(r"\D", "", phone)
    r = requests.get(f"{BAILEYS}/accounts/mark/resolve", params={"phone": digits}, timeout=30).json()
    if not r.get("exists"):
        return False, None, "number not on WhatsApp"
    r = requests.post(f"{BAILEYS}/accounts/mark/send", json={"phone": digits, "text": text}, timeout=120)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return (True, j.get("msgId"), None) if r.ok and j.get("ok") else (False, None, f"HTTP {r.status_code}: {str(j)[:200]}")


def send_email(to: str, subject: str | None, text: str) -> tuple[bool, str | None, str | None]:
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    if not pw:
        return False, None, "no GMAIL_APP_PASSWORD yet"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Mark Mirimsky <{GMAIL}>", to, subject or "Property enquiry"
    msg.set_content(text if re.search(r"Mark\s*$", text.strip()) else text + "\n\nMark Mirimsky")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(GMAIL, pw)
        s.send_message(msg)
    return True, msg["Message-ID"], None


def run_once(w: WorkerClient) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    st = state()
    queue = w.request("GET", "/sync/outreach/queue?status=approved")["messages"]
    if not queue:
        return "queue empty"
    done = []
    # One WhatsApp per run, only in hours and after the random gap.
    wa = [m for m in queue if m["channel"] == "whatsapp"]
    if wa and in_hours(now) and now.timestamp() >= st.get("wa_next", 0):
        m = wa[0]
        ok, ext, err = send_whatsapp(m["to_address"], m["body"])
        w.request("POST", "/sync/outreach/sent", {"id": m["id"], "status": "sent" if ok else "failed", "external_id": ext, "error": err})
        st["wa_next"] = now.timestamp() + random.randint(4 * 60, 9 * 60)
        done.append(f"whatsapp {m.get('agency')} → {'sent' if ok else err}")
    # Emails: one per run too, 1–3 minutes apart (no daily cap).
    prior = set(json.loads((Path(__file__).resolve().parents[1] / "data" / "prior_contacts.json").read_text())["domains"])
    sent_doms = {(m.get("to_address") or "").split("@")[-1].lower() for m in w.request("GET", "/sync/outreach/queue?status=sent")["messages"]
                 if m["channel"] == "email"}
    for m in [m for m in queue if m["channel"] == "email"]:
        d = (m["to_address"] or "").split("@")[-1].lower()
        if d in prior or d in sent_doms or not re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", m["to_address"] or ""):
            w.request("POST", "/sync/outreach/sent", {"id": m["id"], "status": "cancelled", "error": "agency already contacted before / bad address"})
            queue.remove(m)
    em = [m for m in queue if m["channel"] == "email"]
    if em and in_hours(now) and now.timestamp() >= st.get("mail_next", 0) and os.environ.get("GMAIL_APP_PASSWORD"):
        m = em[0]
        try:
            ok, ext, err = send_email(m["to_address"], m.get("subject"), m["body"])
        except (smtplib.SMTPException, OSError) as e:
            ok, ext, err = False, None, str(e)[:200]
        w.request("POST", "/sync/outreach/sent", {"id": m["id"], "status": "sent" if ok else "failed", "external_id": ext, "error": err})
        st["mail_next"] = now.timestamp() + random.randint(60, 180)
        done.append(f"email {m.get('agency')} → {'sent' if ok else err}")
    # Contact forms: one per run, 2–5 minutes apart (no daily cap). CAPTCHA forms are never attempted.
    fm = [m for m in queue if m["channel"] == "form"]
    if fm and in_hours(now) and now.timestamp() >= st.get("form_next", 0):
        m = fm[0]
        try:
            ok, note = submit_form(m["to_address"], m["body"], m.get("subject") or "Property enquiry")
        except (requests.RequestException, ValueError) as e:
            ok, note = False, str(e)[:200]
        w.request("POST", "/sync/outreach/sent", {"id": m["id"], "status": "sent" if ok else "failed", "external_id": None, "error": note})
        st["form_next"] = now.timestamp() + random.randint(120, 300)
        done.append(f"form {m.get('agency')} → {note}")
    save(st)
    return "; ".join(done) or ("waiting (hours / pacing)" if in_hours(now) else "outside sending hours")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    w = WorkerClient(timeout=120)
    print(dt.datetime.now().strftime("%F %T"), run_once(w), flush=True)


if __name__ == "__main__":
    main()

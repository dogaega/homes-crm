"""
Daily outreach planner: for every active client request, find new matching
properties, work out which agency holds each mandate, choose whom to write to
(network-growing and pacing rules), have a free LLM write a unique message from
that contact's history, and queue it as a draft for Mark. Nothing is sent here.

    python -m outreach.daily [--per-request 6] [--whatsapp-cap 20] [--dry-run]

Rules (Mark, 2026-10-13):
- prefer a NEW person at the agency (grow the network); the listing agent only
  for the first contact with an agency;
- at most one message per person per week, never while the last one is
  unanswered; 2 unanswered days in a row pause the person for 30 days;
- one bundled message per agency group per day;
- every agency gets an email, its contact form and (capped, default 8/day)
  a WhatsApp; emails and forms ask them to reply on Mark's WhatsApp;
- only ever ask whether it is still available (optionally a brochure) —
  never a viewing, a date or any commitment;
- English, written as an interested private person to new contacts; contacts
  who already know Mark as an agent are written to consistently with that.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach.contact_finder import find as find_contacts  # noqa: E402
from outreach.llm import chat_json  # noqa: E402
from sync import netwait  # noqa: E402,F401
from sync.worker_client import WorkerClient  # noqa: E402

PORTALS = re.compile(r"properties\.lefigaro\.com|immobilier\.notaires\.fr", re.I)
MOBILE = re.compile(r"^(?:\+?33|0)\s*[67]|^\+?377\s*[46]")
NOW = dt.datetime.now(dt.timezone.utc)
MY_WA = "+357 96 146814"  # Mark's WhatsApp: emails and forms ask agencies to reply there


def clean_name(x) -> str | None:
    """'Nicolas\n\t\tTOURNIAIRE Responsable' → 'Nicolas Tourniaire'; form labels are not names."""
    n = re.sub(r"\s+", " ", str(x or "")).strip()
    n = re.sub(r"\b(responsable|mobile|portable|agent|commercial|n[ée]gociat\w*|directeur|directrice|manager|gérant\w*|t[ée]l\w*)\b.*$", "", n, flags=re.I).strip(" -–,:")
    if not n or re.search(r"rappel|demande|contact|agence|agency|immobili|appartement|apartment|villa|ch[âa]teau|cave|nouveaut|"
                          r"riviera|monaco|antibes|cannes|nice\b|menton|france|maison|studio|r[ée]sidence|propri[ée]t|bureau|facebook|"
                          r"instagram|linkedin|english|fran[çc]ais|director|directeur|office|accueil|service|\d", n, re.I) or len(n) > 40:
        return None
    if not re.fullmatch(r"[A-Za-zÀ-ÿ'’.\- ]{2,40}", n) or len(n.split()) > 4:
        return None
    return " ".join(w.capitalize() if w.isupper() and len(w) > 2 else w for w in n.split())


def clean_email(x) -> str | None:
    from urllib.parse import unquote
    e = unquote(unquote(str(x or ""))).strip().lower()
    if re.search(r"exemple|example|domaine\.|votre|youremail|^email@|^nom@|noreply|no-reply|^friend@|@email\.com$|@mail\.com$|^test@|^xxx", e):
        return None
    return e if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e) else None


def digits(x) -> str:
    return re.sub(r"\D", "", str(x or ""))


def iso_age_days(s: str | None) -> float:
    if not s:
        return 1e9
    try:
        return (NOW - dt.datetime.fromisoformat(s.replace("Z", "+00:00"))).total_seconds() / 86400
    except ValueError:
        return 1e9


def criteria(r: dict) -> dict | None:
    c = r["c"]
    if c.get("location_unrecognized") or c.get("commercial") or not c.get("cities"):
        return None
    return {"no": r["id"], "tx": c.get("transaction_type") or "sale", "towns": c["cities"], "quarters": c.get("quarters") or [],
            "villa": c.get("kind") == "villa", "apt": c.get("kind") == "apartment", "beds": c.get("bedrooms_min"),
            "area": c.get("area_min"), "budget": c.get("price_max")}


PRIORITY = json.loads((Path(__file__).resolve().parents[1] / "data" / "request_priority.json").read_text())
HOT = re.compile(r"срочно|готов смотреть|смотреть сейчас|актуально|asap|urgent|сейчас", re.I)


def priority(r: dict) -> tuple[int, str]:
    """Score a client request so the hottest ones get outreach first. -1 = skip for now."""
    c, no = r["c"], r["c"].get("sheet_no") or ""
    if no in PRIORITY.get("skip", {}):
        return -1, PRIORITY["skip"][no]
    if (c.get("status") or "").strip() == "Уточнить":
        return -1, "needs details first (status Уточнить)"
    score, why = 0, []
    if c.get("deadline"):
        try:
            days = (dt.datetime.strptime(c["deadline"], "%d.%m.%Y").date() - dt.date.today()).days
            if days <= 14:
                score += 40; why.append(f"deadline {c['deadline']}")
        except ValueError:
            pass
    if no in PRIORITY.get("artem_active", []):
        score += 30; why.append("on Artem's call cards")
    text = f"{c.get('notes') or ''} {c.get('location_text') or ''}"
    recent = [d for d in re.findall(r"\b(\d{1,2})\.(\d{2})\b", text)
              if 0 <= (dt.date.today() - dt.date(dt.date.today().year, int(d[1]), int(d[0]))).days <= 7] if text else []
    if HOT.search(text) or recent:
        score += 15; why.append("recent / urgent note")
    score += {"Подбор": 10, "Новый": 5}.get((c.get("status") or "").strip(), 0)
    if c.get("price_max") and c.get("cities"):
        score += 10
    else:
        why.append("vague criteria")
    return score, ", ".join(why) or "normal"


def on_whatsapp(phone: str) -> bool:
    """Ask the local Baileys service whether the number has WhatsApp (no message is sent)."""
    import requests
    try:
        r = requests.get("http://localhost:8850/accounts/mark/resolve", params={"phone": digits(phone)}, timeout=20)
        return bool(r.json().get("exists"))
    except (requests.RequestException, ValueError):
        return False


PRIOR = json.loads((Path(__file__).resolve().parents[1] / "data" / "prior_contacts.json").read_text())
_SENT_CACHE: list | None = None


def _dom(x: str | None) -> str:
    x = (x or "").lower().strip()
    x = x.split("@")[-1] if "@" in x else re.sub(r"^https?://(www\.)?", "", x).split("/")[0]
    return x.removeprefix("www.")


def already_contacted(ctx: dict, w) -> str | None:
    """Why this agency must not get automated outreach, or None."""
    global _SENT_CACHE
    if ctx.get("history"):
        return f"{len(ctx['history'])} earlier messages"
    doms = {_dom(a.get("website")) for a in ctx.get("agencies", [])} | {_dom(a.get("email")) for a in ctx.get("agencies", [])} \
        | {_dom(p.get("email")) for p in ctx.get("people", [])}
    doms.discard("")
    hit = doms & set(PRIOR["domains"])
    if hit:
        return "emailed before from propertyone / markmirimsky: " + ", ".join(sorted(hit))
    if _SENT_CACHE is None:
        _SENT_CACHE = w.request("GET", "/sync/outreach/queue?status=sent")["messages"]
    ids = {a.get("id") for a in ctx.get("agencies", [])}
    if any(m.get("agency_id") in ids or _dom(m.get("to_address")) in doms for m in _SENT_CACHE):
        return "already got an outreach message"
    return None


def form_usable(url: str) -> bool:
    """A form we can fill without a person (no CAPTCHA, every required field understood)."""
    from outreach.form_filler import submit
    try:
        ok, note = submit(url, "-", dry_run=True)
    except Exception as e:  # noqa: BLE001 — any site error just means: no form
        ok, note = False, str(e)
    if not ok:
        print(f"  form not usable ({note[:80]}): {url}")
    return ok


def mandate_source(prop: dict) -> dict | None:
    """The source to contact: says 'exclusive' > only agency listing it > first listed. Portals never."""
    srcs = [s for s in prop.get("sources", []) if not PORTALS.search(s.get("source_url") or "")]
    if not srcs:
        return None
    excl = [s for s in srcs if s.get("exclusive")]
    if excl:
        return {**excl[0], "why": "says exclusive"}
    agencies = {s.get("agency") for s in srcs}
    if len(agencies) == 1:
        return {**srcs[0], "why": "only agency listing it"}
    return {**sorted(srcs, key=lambda s: s.get("first_seen_at") or "")[0], "why": f"listed first of {len(agencies)} agencies"}


def eligible(p: dict) -> bool:
    if p.get("paused_until") and p["paused_until"] > NOW.isoformat():
        return False
    if (p.get("unanswered_streak") or 0) > 0:  # never while the last message is unanswered
        return False
    return iso_age_days(p.get("last_contacted_at")) >= 7


def choose_person(ctx: dict, src: dict) -> tuple[dict | None, str]:
    """Return (person, why). person = {name, phone, email, contact_id?, persona?}."""
    people = ctx["people"]
    known_phones = {digits(p.get("phone"))[-9:] for p in people if p.get("phone")} | {digits(p.get("whatsapp"))[-9:] for p in people if p.get("whatsapp")}
    known_emails = {(p.get("email") or "").lower() for p in people if p.get("email")}
    talked = [p for p in people if (p.get("sent_count") or 0) > 0]
    new_agents = [a for a in ctx["listing_agents"]
                  if not ((a.get("agent_phone") and digits(a["agent_phone"])[-9:] in known_phones)
                          or (a.get("agent_email") and a["agent_email"].lower() in known_emails))]
    as_person = lambda a: {"name": clean_name(a.get("agent_name")), "phone": a.get("agent_whatsapp") or a.get("agent_phone"),
                           "email": clean_email(a.get("agent_email")), "contact_id": None}
    listing_agent = {"name": clean_name(src.get("agent_name")), "phone": src.get("agent_whatsapp") or src.get("agent_phone"),
                     "email": clean_email(src.get("agent_email")), "contact_id": None}
    if not talked:  # first contact with this agency: the agent who knows the flat
        if listing_agent["phone"] or listing_agent["email"]:
            return listing_agent, "first contact with the agency: the listing agent"
        if new_agents:
            return as_person(new_agents[0]), "first contact with the agency: an agent from its listings"
        gen = {"name": None, "phone": src.get("agency_phone") or src.get("a_phone"), "email": clean_email(src.get("agency_email") or src.get("a_email")), "contact_id": None}
        return (gen, "first contact: agency office") if gen["phone"] or gen["email"] else (None, "no contact found")
    # Known agency: grow the network — someone we have never written to.
    for a in new_agents:
        if a.get("agent_phone") or a.get("agent_email") or a.get("agent_whatsapp"):
            return as_person(a), "new colleague at an agency we already know"
    warm = sorted([p for p in people if eligible(p)], key=lambda p: p.get("last_contacted_at") or "")
    if warm:
        p = warm[0]
        return {"name": p.get("name"), "phone": p.get("whatsapp") or p.get("phone"), "email": p.get("email"), "contact_id": p["id"],
                "persona": p.get("persona")}, "known contact, not written to for a week"
    return None, "everyone at this agency was contacted recently or has not replied — wait"


DRAFT = """Write ONE message from Mark to a real-estate agent on the French Riviera / Monaco, in MARK'S OWN STYLE.
Questions, always both: (1) is each listed property still available, and (2) do they have it directly — their own mandate
from the owner / exclusive (for several properties: which ones they have directly or exclusively). Optionally also ask for the brochure.
Mark's own ways of asking (2): "do you have this one straight from the owner?", "Are all those exclusive?", "could you let me know
which ones are exclusive", "do you have it directly with the owner".
NEVER ask for, offer or suggest a viewing or visit, never mention dates, days or timing, never make any commitment or proposal.

Mark's style (copy it, these are his real messages to agents):
- "Hi Angie, I came across your 3-room apartment for rent in Beausoleil (€1,990/month): <link> Is it still available? Thank you!"
- "Hello, do you work for rent also?"
- "Its for my client, in case you have a brochure could you send please so I can show them"
- "Could you send the brochures please for these apartments? So I could show to my client"
- "Hello do you have this one straight from the owner?"
- "Good day, we got a client looking in monaco for rent at 6000, it can be one bedroom"
So: plain, short, simple everyday English, a bit casual, like typing on the phone. Opens with "Hello", "Hi <first name>", "Good day"
or straight with the question. Simple words only — never fancy or salesy wording ("stunning", "exquisite", "I'm keen", "I'd love to",
"impressed", "following up"), no em dashes or en dashes, no semicolons, no emojis. 1-3 sentences. For 3+ properties a short list
with "•" is fine (Mark does that), each "•" item on its own line. Each property: type + town + one or two figures, then its link, nothing more.
Mark may say it's for his client, in his own varied words (e.g. "for my client", "I have a client for it", "Its for a client of mine",
or not at all); don't always use the same phrase. NEVER say his job title, his position or the agency he works for.
Use ONLY the facts given below. Never invent anything (that a colleague mentioned it, where Mark is, his plans, prices or terms not given).
If Mark talked to THIS person before (see history), you may refer to it in a few words; if only colleagues talked to him, you may say
"your colleague X helped me before" at most. Never ask again what was already answered.
Address the recipient by first name only if a name is given; otherwise no name.
Do NOT reuse wording from these recent messages: {avoid}
Recipient: {person} at {agency}{channel_note}
Properties (use these figures exactly):
{props}
History with this agency (newest first, "OUT" = Mark):
{history}
{channel_rule}
Return ONLY JSON: {{"message": "...", "subject": "short email subject or null"}}"""


def draft_message(person: dict, ctx: dict, src_agency: str, props: list[dict], channel: str, avoid: list[str]) -> dict | None:
    persona = person.get("persona") or ("agent" if any((p.get("persona") == "agent") for p in ctx["people"] if (p.get("sent_count") or 0) > 0) else "private")
    persona_rule = ("Mark is a broker working for a client; he may say 'for my client'." if persona == "agent" else
                    "Write as an interested private person: do NOT mention clients, an agency or brokerage.")
    line = lambda h: f"{(h.get('at') or '')[:10]} {'OUT' if h['direction'] == 'out' else 'IN '} {h.get('person') or ''}: {(h.get('text') or '')[:220]}"
    mine = [h for h in ctx["history"] if person.get("phone") and digits(h.get("phone"))[-9:] == digits(person["phone"])[-9:]]
    others = [h for h in ctx["history"] if h not in mine]
    hist = ("With " + (person.get("name") or "this person") + " directly:\n" + ("\n".join(line(h) for h in mine[:15]) or "(never talked)")
            + "\n\nWith their colleagues at the same agency (not with this person!):\n" + ("\n".join(line(h) for h in others[:12]) or "(none)"))
    lines = []
    for p in props:
        bits = [p.get("city"), (p.get("property_name") or "")[:60] or None,
                f"{int(p['living_area_sqm'])} m²" if p.get("living_area_sqm") else None,
                f"{p['bedrooms']} bed" if p.get("bedrooms") else None,
                f"€{int(p['price']):,}" + ("/month" if p.get("tx") == "rent" else "") if p.get("price") else None,
                p["_src"].get("source_url")]
        lines.append("- " + ", ".join(b for b in bits if b) + (" [mandate known: listing says exclusive]" if p["_src"].get("exclusive") else ""))
    prompt = DRAFT.format(ies="ies" if len(props) > 1 else "y", is_are="are" if len(props) > 1 else "is", persona_rule=persona_rule,
                          avoid=json.dumps(avoid[-6:], ensure_ascii=False), person=person.get("name") or "the agent", agency=src_agency,
                          channel_note={"whatsapp": " (WhatsApp)", "email": " (email)", "form": " (the contact form on their website)"}[channel],
                          channel_rule="" if channel == "whatsapp" else
                          f"End with a short line asking them to reply on WhatsApp at {MY_WA} (write the number exactly like that), then 'Thank you' and 'Mark'.",
                          purpose="first time asking about these properties", props="\n".join(lines), history=hist)
    for _ in range(3):  # rewrite drafts that break the rules
        out = chat_json(prompt, temperature=0.8)
        msg = (out or {}).get("message") or ""
        problems = []
        if re.search(r"mark here|i'?m mark\b|i am mark\b|this is mark\b|it'?s mark\b|^\s*hi, mark|new here|private buyer", msg[:70], re.I):
            problems.append("starts with a banned opener")
        missing = [p["_src"]["source_url"] for p in props if p["_src"].get("source_url") and p["_src"]["source_url"] not in msg]
        if missing:
            problems.append("missing links")
        if re.search(r"\b(paris|london|moving|relocat|my wife|my husband|my family|my kids|flying|coming from|visiting from)\b", msg, re.I):
            problems.append("invented personal facts")
        bare = re.sub(r"https?://\S+", " ", msg)
        if re.search(r"\bviewings?\b|\bvisit|\bview (it|them|the)|\bsee (it|them|the)|arrange|schedul|this week|next week|tomorrow|come (by|and see)|\btour\b", bare, re.I):
            problems.append("asks for a viewing / mentions timing — only ask about availability")
        if channel != "whatsapp" and "146814" not in msg.replace(" ", ""):
            problems.append(f"must ask them to reply on WhatsApp at {MY_WA}")
        if re.search(r"[—–;\u2011]|stunning|exquisite|keen|i'?d love|impressed|following up|colleague mentioned|mentioned to me|my colleague|my associate|my partner|we spoke|as discussed", bare, re.I):
            problems.append("not Mark's style (dashes / semicolons / fancy words) or invented a mention")
        if not re.search(r"direct|exclusiv|mandate|owner", bare, re.I):
            problems.append("must also ask whether they have it directly / exclusively (mandate)")
        if "availab" not in bare.lower() and "still on the market" not in bare.lower():
            problems.append("must ask whether it is still available")
        if any(msg[:60].lower() == a[:60].lower() for a in avoid):
            problems.append("same opening as another message")
        if msg and not problems:
            return out
        print(f"  redraft ({', '.join(problems)})", flush=True)
        prompt += f"\n\nYour previous attempt was rejected ({', '.join(problems)}). Write it again, fixing that."
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-request", type=int, default=6, help="new properties per client per run")
    ap.add_argument("--whatsapp-cap", type=int, default=0, help="new WhatsApp chats a day (0: Mark's WhatsApp was blocked 14.10 — email only)")
    ap.add_argument("--forms", action="store_true", help="also fill contact forms (off: email only, Mark 14.10)")
    ap.add_argument("--max-agencies", type=int, default=None, help="only the first N agencies (for a quick review)")
    ap.add_argument("--dry-run", action="store_true", help="draft and print, queue nothing")
    args = ap.parse_args()
    w = WorkerClient(timeout=180)
    reqs = w.request("GET", "/sync/outreach/requests")["requests"]
    reqs = [r for r in reqs if r["c"].get("sheet_no")]  # "Запросы клиентов" rows only (not retired WORK FRANCE ones)
    # Hottest clients first (deadline, Artem's call cards, recent notes); skipped ones (e.g. Mareterra for now) get nothing.
    prio = {r["id"]: priority(r) for r in reqs}
    for r in sorted(reqs, key=lambda r: -prio[r["id"]][0]):
        print(f"  priority {prio[r['id']][0]:>3}  №{r['c'].get('sheet_no')} {r['name'][:40]} — {prio[r['id']][1]}")
    reqs = [r for r in reqs if prio[r["id"]][0] >= 0]
    crit = [c for c in (criteria(r) for r in reqs) if c]
    names = {r["id"]: f"{r['name']} (№{r['c'].get('sheet_no') or '?'})" for r in reqs}
    matches = w.request("POST", "/sync/match-clients", {"clients": crit, "limit": 25})["matches"]

    # New properties per client → grouped by the agency that holds the mandate.
    by_agency: dict[str, dict] = {}
    for rid, props in matches.items():
        fresh = set(w.request("POST", "/sync/outreach/options", {"request_id": rid, "property_ids": [p["id"] for p in props],
                                                                  "origin": "crm_match"})["new"]) if not args.dry_run else {p["id"] for p in props}
        taken = 0
        for p in props:
            score = prio.get(rid, (0, ""))[0]
            quota = args.per_request * 2 if score >= 50 else args.per_request if score >= 20 else 1
            if p["id"] not in fresh or taken >= quota:
                continue
            src = mandate_source(p)
            if not src or not src.get("agency"):
                continue
            # Town sanity: a "Monaco" property whose own listing URL names another town is mislabelled — skip it.
            urls = " ".join(s.get("source_url") or "" for s in p.get("sources", [])).lower()
            if p.get("city") == "Monaco" and re.search(r"[/\-_+](nice|cannes|antibes|menton|cagnes)[\-_/+]", urls) and "monaco" not in urls:
                continue
            g = by_agency.setdefault(src["agency"], {"src": src, "props": {}, "requests": set()})
            g["props"].setdefault(p["id"], {**p, "_src": src})
            g["requests"].add(rid)
            taken += 1

    # Agency ids for context lookups.
    # The WhatsApp cap is per day: count what is already waiting or went out today.
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    waiting = w.request("GET", "/sync/outreach/queue?status=approved")["messages"]
    sent = w.request("GET", "/sync/outreach/queue?status=sent")["messages"]
    wa_used = sum(1 for m in waiting if m["channel"] == "whatsapp") + sum(1 for m in sent if m["channel"] == "whatsapp" and (m.get("sent_at") or "")[:10] == today)
    drafts, avoid = [], [m["body"][:160] for m in waiting[-6:]]
    # Agencies serving the hottest clients go first (WhatsApp cap is spent on them), then by how many properties they hold.
    rank = lambda g: (max(prio.get(r, (0, ""))[0] for r in g["requests"]), len(g["props"]))
    for agency, g in sorted(by_agency.items(), key=lambda kv: rank(kv[1]), reverse=True)[:args.max_agencies]:
        src = g["src"]
        agency_id = src.get("agency_id")
        if not agency_id:
            hit = [s for p in g["props"].values() for s in p.get("sources", []) if s.get("agency") == agency and s.get("agency_id")]
            agency_id = hit[0]["agency_id"] if hit else None
        if not agency_id:
            continue
        ctx = w.request("POST", "/sync/outreach/context", {"agency_id": agency_id})
        # Hard stop (Mark, 2026-10-13): never auto-contact an agency Mark (or the system) already talked to —
        # any WhatsApp / email / call history, a propertyone / markmirimsky email, or an earlier outreach message.
        known = already_contacted(ctx, w)
        if known:
            print(f"skip {agency}: already in contact ({known})")
            continue
        person, why_person = choose_person(ctx, src)
        # Every channel for every agency (Mark, 2026-10-13): email + contact form + WhatsApp (WhatsApp capped low).
        site = next((a.get("website") for a in ctx.get("agencies", []) if a.get("id") == agency_id), None)
        f = find_contacts(agency_id, src.get("source_url"), site, client=None if args.dry_run else w)
        if not person or not (person.get("phone") or person.get("email")):
            if f.get("mobiles") or f.get("emails"):
                p0 = f["people"][0] if f.get("people") else {}
                person = {"name": clean_name(p0.get("name")), "phone": p0.get("phone") or (f["mobiles"][0] if f.get("mobiles") else None),
                          "email": f["emails"][0] if f.get("emails") else None, "contact_id": None}
                why_person = "found on the " + ("listing page" if src.get("source_url") else "agency site")
        person = person or {"name": None, "phone": None, "email": None, "contact_id": None}
        email = person.get("email") or clean_email(src.get("agency_email") or src.get("a_email")) \
            or next((clean_email(e) for e in f.get("emails", []) if clean_email(e)), None)
        if email and not person.get("email"):
            person = {**person, "email": email}
        phone = person.get("phone") or ""
        plan = []
        wa_recent = any(h.get("channel") == "whatsapp" and h["direction"] == "out" and iso_age_days(h.get("at")) < 7 for h in ctx["history"])
        if phone and not wa_recent and MOBILE.search(phone.replace(" ", "")) and wa_used < args.whatsapp_cap and on_whatsapp(phone):
            plan.append(("whatsapp", phone))
        if email:
            plan.append(("email", email))
        if args.forms and f.get("form") and form_usable(f["form"]):
            plan.append(("form", f["form"]))
        if not plan:
            print(f"skip {agency}: {why_person}; no WhatsApp, email or form")
            continue
        props = list(g["props"].values())[:4]
        for channel, to in plan:
            who = person if channel != "form" else {**person, "name": None}
            d = draft_message(who, ctx, agency, props, channel, avoid)
            if not d:
                print(f"skip {agency} {channel}: drafting failed")
                continue
            avoid.append(d["message"][:160])
            wa_used += channel == "whatsapp"
            drafts.append({"agency_id": agency_id, "contact_id": person.get("contact_id") if channel != "form" else None, "channel": channel,
                           "person_name": who.get("name"),
                           "status": "approved",  # Mark 2026-10-13: send on their own, he watches the results
                           "account": {"whatsapp": "mark", "email": "markmirimsky@gmail.com", "form": "form"}[channel],
                           "to_address": to, "subject": d.get("subject"), "body": d["message"],
                           "property_ids": [p["id"] for p in props], "request_ids": sorted(g["requests"]), "purpose": "first_contact",
                           "reason": f"{agency} — {src['why']}; {channel} to {who.get('name') or 'office'}: {why_person}; for "
                                     + ", ".join(names.get(r, r) for r in sorted(g["requests"]))})
            print(f"\n=== {agency} → {who.get('name') or 'office'} ({channel} {to})\n{drafts[-1]['reason']}\n---\n{d['message']}", flush=True)
    if drafts and not args.dry_run:
        print(w.request("POST", "/sync/outreach/queue", {"messages": drafts}))
    print(f"\n{len(drafts)} drafts ({wa_used} WhatsApp) for {len(by_agency)} agencies")


if __name__ == "__main__":
    main()

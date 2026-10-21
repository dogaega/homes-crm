"""
For each client request in Artem's app (data/app_requests.json), find the live
listings that are most likely held DIRECTLY by one agency (mandate / owner):

  1. the listing itself says exclusive / sole agent / mandat exclusif, or
  2. only one agency advertises the property (no other agency lists it).

Listings shared by several agencies without an exclusivity mention are left
out — those are usually not direct. Properties already on Artem's cards are
skipped. Output is a TSV for Mark's Google Sheet (nothing is sent).

    python -m outreach.direct_finder [--per-request 6] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sync.worker_client import WorkerClient  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
# property portals re-advertise other agencies' listings; they are never the mandate holder
PORTALS = re.compile(r"livein|chambre.immobili|seloger|leboncoin|bienici|green.acres|idealista|jamesedition|properstar|logic.immo", re.I)
OTHER_TOWNS = ["nice", "negresco", "promenade des anglais", "cannes", "antibes", "menton", "beausoleil", "cap-d-ail", "eze",
               "villefranche", "roquebrune", "marseille", "paris", "auteuil", "toulon", "saint-tropez"]


def norm(s: str | None) -> str:
    return unicodedata.normalize("NFD", (s or "").lower()).encode("ascii", "ignore").decode()


def dom(x: str | None) -> str:
    x = (x or "").lower()
    if "@" in x:
        return x.split("@")[1]
    return re.sub(r"^https?://(www\.)?", "", x).split("/")[0]


def direct_reason(item: dict) -> tuple[int, str, dict] | None:
    """(rank, why, source of the holder) or None when it doesn't look direct."""
    srcs = [s for s in item.get("sources") or [] if not PORTALS.search(f"{s.get('agency') or ''} {s.get('source_url') or ''}")]
    if not srcs:
        return None
    excl = [s for s in srcs if s.get("exclusive")]
    agencies = {s.get("agency_id") or s.get("agency") for s in srcs}
    if excl:
        return 0, "listing says exclusive", excl[0]
    if len(agencies) == 1:
        return 1, "only this agency lists it", srcs[0]
    return None


TEAM_SHEET = "10nkjBwS9ZsWz2QUOiWsPZV2CV3zPwEms56V8P9DT6UI"


def artem_base() -> list[dict]:
    """Artem's object list («База объектов» in the team sheet): everything he already has, from any agency."""
    import csv
    import io
    import urllib.parse
    import requests
    url = f"https://docs.google.com/spreadsheets/d/{TEAM_SHEET}/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote('База объектов')}"
    return list(csv.DictReader(io.StringIO(requests.get(url, timeout=60).content.decode("utf-8"))))


def in_base(it: dict, base: list[dict]) -> str:
    """Object id when this property is already in Artem's list: same listing URL, or same price and area (±5%)."""
    urls = [(s.get("source_url") or "").split("?")[0].rstrip("/") for s in it.get("sources") or []]
    price, area = it.get("price") or 0, it.get("living_area_sqm") or 0
    for b in base:
        link = (b.get("Ссылка") or "").split("?")[0].rstrip("/")
        if link and any(u and (u == link or u[-30:] == link[-30:]) for u in urls):
            return b["ID"]
        bp = float(re.sub(r"[^\d]", "", b.get("Цена") or "") or 0)
        if not price or bp != price:
            continue
        bph = re.sub(r"\D", "", b.get("Телефон") or "")[-8:]
        if bph and any(bph == re.sub(r"\D", "", str(s.get(k) or ""))[-8:] for s in it.get("sources") or []
                       for k in ("agent_phone", "agency_phone", "a_phone", "agent_whatsapp")):
            return b["ID"]
        m = re.search(r"(\d[\d\s.,]*)\s*м²", b.get("Объект") or "")
        ba = float(re.sub(r"[^\d.]", "", (m.group(1) if m else b.get("Площадь") or "").replace(",", ".")) or 0)
        if (area and ba and abs(ba - area) <= 0.05 * area) or (not area or not ba) and norm(it.get("city")) in norm(b.get("Город")):
            return b["ID"]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-request", type=int, default=6)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cfg = json.loads((DATA / "app_requests.json").read_text())
    skip = [norm(x) for x in cfg.get("already_on_cards", [])]
    prior = json.loads((DATA / "prior_contacts.json").read_text())
    prior_doms = {dom(x) for k in ("emails", "domains") for x in prior.get(k, [])}
    base = artem_base()
    w = WorkerClient(timeout=240)
    clients = []
    for i, r in enumerate(cfg["requests"]):
        clients.append({"no": str(i), "tx": r["tx"], "towns": r["towns"], "quarters": [], "villa": bool(r.get("villa")),
                        "apt": bool(r.get("apt")), "beds": r.get("beds"), "area": r.get("area"), "budget": r.get("budget")})
    matches = {}
    for c in clients:
        matches.update(w.request("POST", "/sync/match-clients", {"clients": [c], "limit": 80})["matches"])
    # Priority order, like the "Today" tab: 🔥 clients in Artem's app first, then the others as the app lists them.
    fire = json.loads((DATA / "request_priority.json").read_text()).get("app_fire", [])
    order = sorted(range(len(cfg["requests"])), key=lambda i: (str(cfg["requests"][i].get("no") or "") not in fire, i))
    rows = []
    for i in order:
        r = cfg["requests"][i]
        picked = []
        for it in matches.get(str(i)) or []:
            text = norm(" ".join(str(it.get(k) or "") for k in ("building_name", "property_name"))
                        + " " + " ".join(f"{s.get('listing_title') or ''} {s.get('source_url') or ''}" for s in it.get("sources") or []))
            if norm(it.get("city")) not in {norm(t) for t in r["towns"]}:
                continue
            # some listings carry the wrong city (Nice "Carré d'Or" filed as Monaco): drop any naming another town
            if any(re.search(rf"\b{t}\b", text) for t in OTHER_TOWNS if t not in {norm(x) for x in r["towns"]}):
                continue
            if re.search(r"suspend|\bvendu|\bsold\b|\bloue\b|\brented\b|under offer|sous compromis|sous offre", text):
                continue
            if r.get("building") and r["building"] not in text:
                continue
            if any(k and k in text for k in skip) or in_base(it, base):
                continue
            d = direct_reason(it)
            if d:
                picked.append((d[0], -(1 if it.get("first_seen_at") else 0), it, d))
        picked.sort(key=lambda x: (x[0], x[1]))
        seen, n = set(), 0
        for _, _, it, (rank, why, s) in picked:
            if n >= args.per_request:
                break
            what = ", ".join(x for x in [
                it.get("building_name") or it.get("property_name") or it.get("quarter") or it.get("city"),
                it.get("city") if it.get("building_name") else None,
                f"{it['bedrooms']} bed" if it.get("bedrooms") else None,
                f"{round(it['living_area_sqm'])} m²" if it.get("living_area_sqm") else None,
                f"€{int(it['price']):,}" + ("/month" if r["tx"] == "rent" else "") if it.get("price") else None] if x)
            if (s.get("agency"), what) in seen:
                continue
            seen.add((s.get("agency"), what))
            n += 1
            emails = s.get("agent_email") or s.get("agency_email") or s.get("a_email") or ""
            known = any(d and d in prior_doms for d in (dom(emails), dom(s.get("website")), dom(s.get("source_url"))))
            one = lambda v: re.sub(r"\s+", " ", str(v or "")).strip()
            rows.append([r["client"], r.get("note") or "", what, why, one(s.get("agency")), one(s.get("agent_name")),
                         one(s.get("agent_phone") or s.get("agent_whatsapp") or s.get("agency_phone") or s.get("a_phone")),
                         one(emails), s.get("source_url") or "", "yes" if known else ""])
    hdr = ["Client (internal)", "Request details", "Property", "Why direct", "Agency", "Agent", "Phone", "Email", "Listing", "We contacted this agency before"]
    cell = lambda v: (lambda t: f'"{t}"' if any(c in t for c in '\n\t"') else t)(str(v).replace('"', '""'))
    tsv = "\n".join("\t".join(cell(c) for c in row) for row in [hdr] + rows)
    if args.out:
        Path(args.out).write_text(tsv)
    by = {}
    for row in rows:
        by[row[0]] = by.get(row[0], 0) + 1
    print(f"{len(rows)} direct candidates:", ", ".join(f"{k} {v}" for k, v in by.items()))


if __name__ == "__main__":
    main()

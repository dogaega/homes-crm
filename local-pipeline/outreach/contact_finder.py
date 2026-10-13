"""
Find how to reach an agency: mobile / WhatsApp numbers, email addresses
(including obfuscated ones) and the contact form, read from the listing page
first (network branches put their own contacts there) and then the agency's
website, contact and team pages. Results are saved on the agency in the CRM
and cached for 30 days.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

CACHE = Path(__file__).resolve().parents[1] / "data" / "contact_finder_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"}
MOBILE = re.compile(r"(?<![\d.])(?:\+33\s?\(?0?\)?\s?[67]|0[67]|\+377\s?[46]|00377\s?[46])(?:[\s.\-]?\d{2}){4}(?![\d.])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
JUNK_EMAIL = re.compile(r"dpo@|^friend@|@email\.com$|noreply|no-reply|example|exemple|domaine\.|votre|youremail|email@|nom@|name@|sentry|wixpress|@2x|\.(png|jpe?g|webp|svg|gif)$|rgpd|privacy|cnil|webmaster|support@", re.I)
CONTACT_LINK = re.compile(r"contact|nous-contacter|equipe|team|about|agence|qui-sommes|our-team|l-equipe", re.I)
NAME_NEAR = re.compile(r"([A-ZÀ-Ý][a-zà-ÿ]+(?:[- ][A-ZÀ-Ý][a-zà-ÿ]+)?\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]{1,})\W{0,60}$")


def _cfemail(enc: str) -> str:
    k = int(enc[:2], 16)
    return "".join(chr(int(enc[i:i + 2], 16) ^ k) for i in range(2, len(enc), 2))


def _norm_phone(p: str) -> str:
    d = re.sub(r"\D", "", p)
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0"):
        d = "33" + d[1:]
    if d.startswith("330"):
        d = "33" + d[3:]
    return "+" + d


def read(url: str) -> dict:
    try:
        r = requests.get(url, headers=UA, timeout=25)
    except requests.RequestException:
        return {}
    if r.status_code >= 400:
        return {}
    h = r.text
    h = re.sub(r"<svg.*?</svg>|<path[^>]*>|<script.*?</script>|<style.*?</style>", " ", h, flags=re.S | re.I)
    raw = html.unescape(unquote(h))
    emails = {e.lower() for e in EMAIL.findall(raw)} | {_cfemail(x).lower() for x in re.findall(r'data-cfemail="([0-9a-f]+)"', h)}
    emails = sorted(e for e in emails if not JUNK_EMAIL.search(e))
    text = re.sub(r"<[^>]+>", " ", raw)
    people, mobiles = [], []
    for m in MOBILE.finditer(text):
        ph = _norm_phone(m.group(0))
        if ph in mobiles:
            continue
        mobiles.append(ph)
        nm = NAME_NEAR.search(text[max(0, m.start() - 120):m.start()])
        if nm and not re.search(r"contact|agence|agency|facebook|instagram|linkedin|english|fran[çc]ais|france|monaco|menton|nice|cannes|"
                                r"antibes|director|directeur|office|accueil|service|t[ée]l|mobile|appel|demande", nm.group(1), re.I):
            people.append({"name": nm.group(1).strip(), "phone": ph, "whatsapp": ph})
    for w in re.findall(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)\+?(\d{9,15})", h):
        ph = "+" + w
        if ph not in mobiles:
            mobiles.append(ph)
    form = bool(re.search(r"<form[^>]*>.*?<textarea", r.text, re.S | re.I))
    links = [urljoin(url, a) for a in re.findall(r'href="([^"#]+)"', r.text) if CONTACT_LINK.search(a)]
    return {"emails": emails, "mobiles": mobiles, "people": people, "form": url if form else None, "links": links}


def find(agency_id: str, listing_url: str | None, website: str | None, client=None, max_age_days: int = 30) -> dict:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    hit = cache.get(agency_id)
    if hit and (dt.datetime.now() - dt.datetime.fromisoformat(hit["at"])).days < max_age_days:
        return hit
    found = {"emails": [], "mobiles": [], "people": [], "form": None}
    pages = [u for u in (listing_url, website) if u]
    seen = set()
    while pages and len(seen) < 6:
        u = pages.pop(0)
        if u in seen:
            continue
        seen.add(u)
        r = read(u)
        for k in ("emails", "mobiles"):
            found[k] += [x for x in r.get(k, []) if x not in found[k]]
        found["people"] += [p for p in r.get("people", []) if p["phone"] not in {q["phone"] for q in found["people"]}]
        found["form"] = found["form"] or r.get("form")
        site = website or (f"{urlparse(u).scheme}://{urlparse(u).netloc}/" if u else None)
        for l in r.get("links", [])[:6]:
            if site and urlparse(l).netloc == urlparse(site).netloc and l not in seen:
                pages.append(l)
    found["at"] = dt.datetime.now().isoformat()
    cache[agency_id] = found
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    if client and (found["emails"] or found["mobiles"] or found["form"]):
        client.request("POST", "/sync/outreach/agency-contacts", {
            "agency_id": agency_id, "website": website, "email": found["emails"][0] if found["emails"] else None,
            "whatsapp": found["mobiles"][0] if found["mobiles"] else None, "form_url": found["form"], "people": found["people"]})
    return found

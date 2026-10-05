"""
Find the website of each real agency in data/sirene_agencies.json with the
Brave Search API (BRAVE_SEARCH_API_KEY in .env). Skips sole traders (network
agents), property managers / holdings, and franchise branches of networks we
already scrape; goes town by town in priority order; resumable.
Results → data/discovered_websites.json.

    python recon/find_websites.py --max 1800
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sync.netwait import wait_for_network  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = DATA / "discovered_websites.json"
API = "https://api.search.brave.com/res/v1/web/search"

MANAGERS = re.compile(r"syndic|gestion|administrat|copropri|patrimoine|holding|\bsci\b|location saisonni|conciergerie", re.I)
# Networks scraped through their national site: their branches need no own search.
NETWORKS = re.compile(r"\borpi\b|century ?21|lafor[eê]t|guy hoquet|\bera\b|st[ée]phane plaza|foncia|citya|nexity|square habitat|"
                      r"\biad\b|safti|capifrance|optimhome|efficity|sotheby|barnes|john taylor|engel|savills|zingraf|carlton|"
                      r"coldwell|kretz|arthur ?immo|l'adresse|cimm|propri[ée]t[ée]s priv[ée]es", re.I)
# Directories, portals, social networks: never an agency's own site.
NOT_OWN_SITE = re.compile(r"societe\.com|pappers|pagesjaunes|annuaire|infogreffe|manageo|verif\.com|corporama|bfmtv|"
                          r"facebook|instagram|linkedin|twitter|x\.com|youtube|tiktok|google\.|wikipedia|"
                          r"seloger|leboncoin|bienici|logic-immo|meilleursagents|figaro|green-acres|pap\.fr|avendrealouer|"
                          r"paruvendu|superimmo|ouestfrance|immobilier\.notaires|proprietes-privees|explorimmo|"
                          r"lefigaro|idealista|kyero|rightmove|jamesedition|luxuryestate|lux-residence|bellesdemeures|"
                          r"trustpilot|tripadvisor|yelp|mappy|118712|118000|hoodspot|cylex|kompass|dnb\.com|"
                          r"data\.gouv|annuaire-entreprises|entreprises\.lefigaro|lesechos|townhall|immomatin|"
                          r"century21|orpi\.com|iadfrance|safti|foncia|laforet|guy-hoquet|stephaneplaza|capifrance", re.I)
PRIORITY = ["MENTON", "ROQUEBRUNE-CAP-MARTIN", "BEAUSOLEIL", "CAP-D'AIL", "EZE", "LA TURBIE", "VILLEFRANCHE-SUR-MER",
            "BEAULIEU-SUR-MER", "SAINT-JEAN-CAP-FERRAT", "NICE", "ANTIBES", "CANNES", "LE CANNET", "MOUGINS", "VALBONNE",
            "SAINT-PAUL-DE-VENCE", "VENCE", "CAGNES-SUR-MER", "VILLENEUVE-LOUBET", "MANDELIEU-LA-NAPOULE", "THEOULE-SUR-MER",
            "SAINT-TROPEZ", "RAMATUELLE", "GASSIN", "GRIMAUD", "SAINTE-MAXIME", "SAINT-RAPHAEL", "FREJUS", "GRASSE"]


def norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def tokens(name: str) -> list[str]:
    stop = {"agence", "immobilier", "immobiliere", "immo", "sarl", "sas", "sasu", "eurl", "real", "estate", "the", "les",
            "des", "and", "cabinet", "groupe", "group", "transactions", "transaction", "france", "cote", "azur", "riviera"}
    return [t for t in re.findall(r"[a-z0-9]{4,}", norm(name)) if t not in stop]


def candidates() -> list[dict]:
    rows = json.loads((DATA / "sirene_agencies.json").read_text())
    seen, out = set(), []
    for r in rows:
        label = f"{r['company']} {r.get('sign') or ''}"
        if r.get("legal_form") == "1000" or MANAGERS.search(label) or NETWORKS.search(label) or r["siren"] in seen:
            continue
        seen.add(r["siren"])
        out.append(r)
    rank = {c: i for i, c in enumerate(PRIORITY)}
    # The 68.31Z code also covers property-owning companies and foreign holdings
    # ("Selecta Cars", "… Anstalt"): names that say "agency" are searched first.
    agencyish = re.compile(r"immo|immobili|agence|agency|real estate|estate|propert|transaction|habitat|home|house|maison|"
                           r"logement|foncier|riviera|azur|villa", re.I)
    out.sort(key=lambda r: (not agencyish.search(f"{r['company']} {r.get('sign') or ''}"),
                            rank.get(norm(r["commune"]).upper().replace("SAINT ", "SAINT-"), len(PRIORITY)), r["commune"] or ""))
    return out


def pick(results: list[dict], name: str, commune: str = "") -> tuple[str | None, str]:
    # A town name in the domain ("…menton.com") says nothing about which agency it is.
    town = set(tokens(commune)) | {"nice", "cannes", "antibes", "menton", "monaco", "mougins", "grasse", "frejus", "toulon"}
    want = [t for t in tokens(name) if t not in town]
    for res in results:
        url = res.get("url") or ""
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if not host or NOT_OWN_SITE.search(host):
            continue
        # The agency's own name must be in the domain: a title match alone also
        # hits news sites and directories that mention the agency.
        if any(t in norm(host).replace("-", "") for t in want):
            return f"https://{urlparse(url).netloc}/", "name in domain"
    return None, "no matching own site in results"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=1800, help="searches this run (free plan: 2,000 / month)")
    args = ap.parse_args()
    key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not key:
        sys.exit("BRAVE_SEARCH_API_KEY missing (local-pipeline/.env)")
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    # Agencies only tried by recon/guess_domains.py (nothing verified) still get a real search.
    todo = [c for c in candidates() if c["siren"] not in done or (done[c["siren"]].get("guessed") and not done[c["siren"]]["website"])]
    print(f"{len(done)} already searched, {len(todo)} to go; this run: {min(args.max, len(todo))}")
    used = 0
    for c in todo[: args.max]:
        name = c.get("sign") or c["company"]
        q = f'"{re.sub(r"[()]", " ", name).strip()}" agence immobilière {c["commune"].title()}'
        for attempt in range(4):
            wait_for_network()
            try:
                r = requests.get(API, params={"q": q, "count": 10, "country": "fr", "search_lang": "fr"},
                                 headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=20)
            except requests.RequestException:
                time.sleep(5)
                continue
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            break
        used += 1
        if r.status_code != 200:
            print("search failed", r.status_code, r.text[:200])
            if r.status_code in (401, 402, 403):
                break  # key or quota problem: stop, keep what we have
            continue
        site, why = pick(r.json().get("web", {}).get("results", []), name, c["commune"] or "")
        done[c["siren"]] = {"name": name, "company": c["company"], "commune": c["commune"], "website": site, "why": why, "query": q}
        if used % 25 == 0:
            OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            print(f"{used} searched, {sum(1 for v in done.values() if v['website'])} websites found", flush=True)
        time.sleep(1.1)  # free plan: 1 request / second
    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print(f"done: {used} searches, {sum(1 for v in done.values() if v['website'])} websites of {len(done)} agencies")


if __name__ == "__main__":
    main()

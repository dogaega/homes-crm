"""
Find the real website of Riviera agencies whose guessed website was wrong
(parked "needs_review" sites in site_configs.json), by looking each agency up
on Google Maps in our own headless browser: name + town → the place's
"Website" link, phone. Polite: one lookup every 12–25 s, cookie banner
answered "Reject all", stops at the first CAPTCHA / unusual-traffic page
(never tries to get past it). Resumable.

Writes data/discovered_maps.json (picked up by recon/onboard.py like the other
discovered_*.json finders).

    python recon/maps_websites.py [--limit 50]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = DATA / "discovered_maps.json"
RIV = re.compile(r"monaco|beausoleil|cap.d.ail|roquebrune|menton|eze|villefranche|beaulieu|saint.jean|cap.ferrat|nice|cannes|antibes|"
                 r"juan|mougins|grasse|cagnes|saint.laurent|vence|saint.paul|valbonne|mandelieu|theoule|biot|vallauris|golfe|saint.tropez|"
                 r"ramatuelle|gassin|sainte.maxime|frejus|saint.raphael|la.turbie|cap.martin|villeneuve.loubet|le.cannet", re.I)
SOCIAL = re.compile(r"facebook|instagram|linkedin|google\.|pagesjaunes|tiktok|youtube|wa\.me|whatsapp", re.I)


def host(u: str | None) -> str:
    return re.sub(r"^https?://(www\.)?", "", (u or "").lower()).split("/")[0]


def todo() -> list[dict]:
    cfg = json.loads((DATA / "site_configs.json").read_text())
    disc = {}
    for f in sorted(DATA.glob("discovered_*.json")):
        if f.name != OUT.name:
            disc.update(json.loads(f.read_text()))
    by = {(v.get("name") or "").upper(): v for v in disc.values()}
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    out = []
    for c in cfg:
        if c.get("status") != "needs_review":
            continue
        d = by.get((c.get("agency") or "").upper()) or {}
        town = d.get("commune") or ""
        if not RIV.search(town) or c["agency"] in done:
            continue
        out.append({"agency": c["agency"], "town": town, "old": c.get("website")})
    return out


def lookup(page, name: str, town: str) -> dict:
    q = urllib.parse.quote(f"{name} {town} agence immobilière")
    page.goto(f"https://www.google.com/maps/search/{q}?hl=fr", wait_until="domcontentloaded", timeout=45000)
    if "consent.google" in page.url:
        btn = page.locator("button:has-text('Tout refuser'), button:has-text('Reject all')").first
        if btn.count():
            btn.click()
            page.wait_for_load_state("domcontentloaded")
    if "/sorry/" in page.url or page.locator("text=unusual traffic").count():
        raise RuntimeError("captcha")
    page.wait_for_timeout(3500)
    # a list of results: open the first one
    first = page.locator("a.hfpxzc").first
    if first.count():
        first.click()
        page.wait_for_timeout(3000)
    site = page.locator("a[data-item-id='authority']").first
    phone = page.locator("button[data-item-id^='phone:tel:']").first
    title = page.locator("h1").first
    return {"website": site.get_attribute("href") if site.count() else None,
            "phone": (phone.get_attribute("data-item-id") or "").replace("phone:tel:", "") if phone.count() else None,
            "maps_name": title.inner_text() if title.count() else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    items = todo()[: args.limit]
    print(f"{len(items)} agencies to look up")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(locale="fr-FR", user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
        for it in items:
            try:
                r = lookup(page, it["agency"], it["town"])
            except RuntimeError:
                print("Google asked for a CAPTCHA — stopping (not bypassing it)")
                break
            except Exception as e:  # noqa: BLE001
                r = {"error": str(e)[:120]}
            w = r.get("website")
            ok = bool(w) and not SOCIAL.search(w) and host(w) != host(it["old"])
            done[it["agency"]] = {"name": it["agency"], "company": it["agency"], "commune": it["town"],
                                  "website": w if ok else None, "phone": r.get("phone"), "maps_name": r.get("maps_name"),
                                  "why": "Google Maps lookup" + ("" if ok else f": no new website ({w or r.get('error') or 'none'})")}
            OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            print(f"{it['agency']} ({it['town']}): {w if ok else '-'}")
            time.sleep(random.uniform(12, 25))
        b.close()


if __name__ == "__main__":
    main()

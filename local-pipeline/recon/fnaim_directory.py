"""
Agency websites from the FNAIM member directory (public pages): every member
agency in the Alpes-Maritimes and the Var, with the website on its FNAIM page.
Network branch sites (Orpi, Century 21 …) and social links are skipped. Results
go into data/discovered_fnaim.json for recon/onboard.py.

    python recon/fnaim_directory.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from find_websites import DATA, NOT_OWN_SITE  # noqa: E402
from sync.netwait import wait_for_network  # noqa: E402

BASE = "https://www.fnaim.fr"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
DEPTS = ["/agences-immobilieres/43-alpes-maritimes-06.htm", "/agences-immobilieres/43-var-83.htm"]
OUT = DATA / "discovered_fnaim.json"  # own file: other finders write discovered_websites.json
IGNORE = re.compile(r"fnaim|cnil|google|facebook|linkedin|youtube|twitter|instagram|w3\.org|gstatic|apple\.com|tiktok|pinterest", re.I)
s = requests.Session()
s.headers["User-Agent"] = UA


def get(url: str) -> str:
    for attempt in range(4):
        wait_for_network()
        try:
            r = s.get(url, timeout=25)
            if r.ok:
                time.sleep(1.2)  # polite: one page every ~1–2 s
                return r.text
        except requests.RequestException:
            pass
        time.sleep(5 * (attempt + 1))
    return ""


def main() -> None:
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    agencies: dict[str, str] = {}
    for dept in DEPTS:
        queue, seen = [BASE + dept], set()
        while queue:
            page = queue.pop(0)
            if page in seen:
                continue
            seen.add(page)
            html = get(page)
            for path in re.findall(r'href="(/agence-immobiliere/(\d+)/[^"#]+\.htm)"', html):
                agencies.setdefault(path[1], BASE + path[0])
            for nxt in re.findall(r'href="(/agences-immobilieres/43-agences\.htm\?[^"]*ip=\d+[^"]*)"', html):
                u = urljoin(BASE, nxt.replace("&amp;", "&")).split("#")[0]
                if "op=AGB_NOM" in u and u not in seen:
                    queue.append(u)
        print(dept, "→", len(agencies), "agencies so far", flush=True)
    added = 0
    for i, (aid, url) in enumerate(agencies.items(), 1):
        key = f"fnaim:{aid}"
        if key in done:
            continue
        html = get(url)
        name = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        name = re.sub(r"<[^>]+>|\s+", " ", name.group(1)).strip() if name else url.rsplit("/", 1)[-1]
        town = re.search(r"/43-([a-z-]+?)-", url)
        site = None
        for href in re.findall(r'href="(https?://[^"]+)"', html):
            h = urlparse(href).netloc.lower().removeprefix("www.")
            if h and not IGNORE.search(h) and not NOT_OWN_SITE.search(h):
                site = f"https://{urlparse(href).netloc.lower()}/"
                break
        done[key] = {"name": name, "company": name, "commune": town.group(1).replace("-", " ").upper() if town else "",
                     "website": site, "why": "FNAIM directory", "query": None}
        added += bool(site)
        if i % 50 == 0:
            OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            print(f"{i}/{len(agencies)} pages, {added} own websites", flush=True)
    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print(f"done: {added} own websites from {len(agencies)} FNAIM agencies")


if __name__ == "__main__":
    main()

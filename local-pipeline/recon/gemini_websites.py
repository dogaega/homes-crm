"""
Agency websites via the Gemini API with Google Search grounding (free tier):
15 register agencies per request; every URL Gemini returns is verified like a
guessed domain (it must exist and its homepage must show the agency's name and
real-estate content), so invented URLs never get through. Agencies already
found by another finder are skipped. Resumable; stops cleanly at the daily
quota. Results → data/discovered_gemini.json (read by recon/onboard.py).

    GEMINI_API_KEY=… python recon/gemini_websites.py [--max-requests 450]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from find_websites import DATA, NOT_OWN_SITE, candidates  # noqa: E402
from guess_domains import verify  # noqa: E402
from sync.netwait import wait_for_network  # noqa: E402

OUT = DATA / "discovered_gemini.json"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
API = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
BATCH = 15

PROMPT = """You find the official websites of real-estate agencies on the French Riviera (Alpes-Maritimes 06 / Var 83).
Use Google Search. For each agency below, give its own official website (home page URL), or null if it has none
or you are not sure. Never guess a URL, never give a directory, portal, social network or franchise-network page
(pagesjaunes, societe.com, seloger, leboncoin, facebook, orpi.com/…, century21.fr/… etc.).
Answer ONLY with a JSON array: [{"id": "<id>", "website": "<url or null>"}].

Agencies (id | name | town):
"""


def found_elsewhere() -> set[str]:
    sirens = set()
    for f in DATA.glob("discovered_*.json"):
        if f.name != OUT.name:
            sirens |= {k for k, v in json.loads(f.read_text()).items() if v.get("website")}
    return sirens


def ask(key: str, batch: list[dict]) -> list[dict] | str:
    lines = "\n".join(f'{c["siren"]} | {c.get("sign") or c["company"]} | {(c["commune"] or "").title()}' for c in batch)
    body = {"contents": [{"parts": [{"text": PROMPT + lines}]}], "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0}}
    for attempt in range(5):
        wait_for_network()
        try:
            r = requests.post(API, params={"key": key}, json=body, timeout=120)
        except requests.RequestException:
            time.sleep(10)
            continue
        if r.status_code == 429:
            if "quota" in r.text.lower() and ("per day" in r.text.lower() or "PerDay" in r.text):
                return "daily quota reached"
            time.sleep(20 * (attempt + 1))
            continue
        if r.status_code != 200:
            return f"HTTP {r.status_code}: {r.text[:200]}"
        text = "".join(p.get("text", "") for p in r.json().get("candidates", [{}])[0].get("content", {}).get("parts", []))
        m = re.search(r"\[.*\]", text, re.S)
        try:
            return json.loads(m.group(0)) if m else []
        except json.JSONDecodeError:
            return []
    return "gave up after retries"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-requests", type=int, default=450)
    args = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY missing (local-pipeline/.env)")
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    skip = found_elsewhere() | set(done)
    todo = [c for c in candidates() if c["siren"] not in skip]
    by_id = {c["siren"]: c for c in todo}
    print(f"{len(todo)} agencies without a website yet; up to {args.max_requests} requests × {BATCH}", flush=True)
    verified = 0
    for i in range(0, min(len(todo), args.max_requests * BATCH), BATCH):
        batch = todo[i:i + BATCH]
        res = ask(key, batch)
        if isinstance(res, str):
            print("stopping:", res, flush=True)
            break
        answers = {str(a.get("id")): a.get("website") for a in res if isinstance(a, dict)}

        def check(c: dict) -> tuple[str, dict]:
            url = answers.get(c["siren"])
            name = c.get("sign") or c["company"]
            site = None
            if isinstance(url, str) and url.startswith("http"):
                host = urlparse(url).netloc.lower().removeprefix("www.")
                if host and not NOT_OWN_SITE.search(host) and verify(host, name):
                    site = f"https://{urlparse(url).netloc.lower()}/"
            return c["siren"], {"name": name, "company": c["company"], "commune": c["commune"], "website": site,
                                "why": "Gemini + Google Search (verified)" if site else f"Gemini: {url or 'none'} (not verified)",
                                "query": None}
        with ThreadPoolExecutor(8) as ex:
            for siren, v in ex.map(check, [by_id[c["siren"]] for c in batch]):
                done[siren] = v
                verified += bool(v["website"])
        OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
        print(f"{i + len(batch)} asked, {verified} websites verified", flush=True)
        time.sleep(6)  # stay under the free tier's per-minute limit
    print(f"done: {verified} verified websites; {len(done)} agencies asked so far")


if __name__ == "__main__":
    main()

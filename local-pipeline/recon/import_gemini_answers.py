"""
Import website answers from the Gemini app: filled CSVs saved in gemini_batches/
(columns id, agency, town, website) and/or lines "ID | website" in answers.txt. Every URL is verified like a guessed domain (exists, and
its homepage shows the agency's name and real-estate content) before it joins
data/discovered_gemini.json for recon/onboard.py. Safe to re-run.

    python recon/import_gemini_answers.py
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from find_websites import DATA, NOT_OWN_SITE, candidates  # noqa: E402
from guess_domains import verify  # noqa: E402

ANSWERS = Path(__file__).resolve().parents[2] / "gemini_batches" / "answers.txt"
OUT = DATA / "discovered_gemini.json"


def main() -> None:
    by_id = {c["siren"]: c for c in candidates()}
    pairs = {}
    # Filled CSVs saved from Gemini (any name in gemini_batches/): id, agency, town, website.
    import csv
    for f in sorted(ANSWERS.parent.glob("*.csv")):
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                sid, url = (row.get("id") or "").strip(), (row.get("website") or "").strip()
                if sid in by_id and url:
                    pairs[sid] = url.strip("<>()[]*`'\".,")
    for line in (ANSWERS.read_text(encoding="utf-8") if ANSWERS.exists() else "").splitlines():
        m = re.match(r"\s*\**\s*(\d{9})\s*\**\s*[|;,:\t]\s*(\S+)", line)
        if m and m.group(1) in by_id:
            pairs[m.group(1)] = m.group(2).strip("<>()[]*`'\".,")
    done = json.loads(OUT.read_text()) if OUT.exists() else {}

    def check(item):
        siren, url = item
        c = by_id[siren]
        name = c.get("sign") or c["company"]
        site = None
        if url.lower() != "none":
            u = url if url.startswith("http") else "https://" + url
            host = urlparse(u).netloc.lower().removeprefix("www.")
            if host and not NOT_OWN_SITE.search(host) and verify(host, name):
                site = f"https://{urlparse(u).netloc.lower()}/"
        return siren, {"name": name, "company": c["company"], "commune": c["commune"], "website": site,
                       "why": "Gemini app (verified)" if site else f"Gemini app: {url} (not verified)", "query": None}

    todo = [(s, u) for s, u in pairs.items() if not (done.get(s) or {}).get("website")]
    with ThreadPoolExecutor(16) as ex:
        for siren, v in ex.map(check, todo):
            done[siren] = v
    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    ok = sum(1 for v in done.values() if v["website"])
    print(f"{len(pairs)} answers read, {len(todo)} checked now; {ok} verified websites in total")


if __name__ == "__main__":
    main()

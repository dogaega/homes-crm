"""
Free website discovery for register agencies not searched yet: guess the
domains an agency would use from its name / sign, keep the ones that exist,
and accept a site only when its homepage shows the agency's name and
real-estate content. Results go into data/discovered_websites.json
(why = "guessed domain"), ready for recon/onboard.py.

    python recon/guess_domains.py
"""

from __future__ import annotations

import json
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from find_websites import DATA, OUT, candidates, norm, tokens  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
TLDS = ("fr", "com")
REAL_ESTATE = re.compile(r"immobili|vente|location|appartement|maison|villa|biens?|properties|real estate|estimation", re.I)
socket.setdefaulttimeout(4)


def slugs(name: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", norm(re.sub(r"\(.*?\)", " ", name)))
             if w not in {"sarl", "sas", "sasu", "eurl", "sa", "snc", "et", "de", "la", "le", "les", "du", "des", "l", "d"}]
    if not words:
        return []
    core = [w for w in words if w not in {"agence", "immobilier", "immobiliere", "immo", "real", "estate", "transactions", "transaction"}]
    # Most likely forms first; 5 names × 2 TLDs keeps it to ~10 lookups per agency.
    out = ["".join(words), "-".join(words)]
    if core:
        c, cj = "".join(core), "-".join(core)
        out += [c, f"{c}-immobilier", f"agence-{cj}"]
    return [s for s in dict.fromkeys(out) if 4 <= len(s) <= 40][:5]


def exists(host: str) -> bool:
    for h in (host, "www." + host):
        try:
            socket.getaddrinfo(h, 443)
            return True
        except OSError:
            continue
    return False


def verify(host: str, name: str) -> bool:
    try:
        r = requests.get(f"https://{host}/", headers={"User-Agent": UA}, timeout=15)
    except requests.RequestException:
        try:
            r = requests.get(f"http://{host}/", headers={"User-Agent": UA}, timeout=15)
        except requests.RequestException:
            return False
    if r.status_code >= 400:
        return False
    text = norm(r.text[:300_000])
    want = tokens(name)
    return bool(want) and all(t in text for t in want[:2]) and bool(REAL_ESTATE.search(text))


def guess(c: dict) -> tuple[str, dict]:
    name = c.get("sign") or c["company"]
    for s in slugs(name):
        for tld in TLDS:
            host = f"{s}.{tld}"
            if exists(host) and verify(host, name):
                return c["siren"], {"name": name, "company": c["company"], "commune": c["commune"],
                                    "website": f"https://{host}/", "why": "guessed domain (verified)", "query": None}
    return c["siren"], {"name": name, "company": c["company"], "commune": c["commune"], "website": None,
                        "why": "no guessed domain verified", "query": None, "guessed": True}


def main() -> None:
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [c for c in candidates() if c["siren"] not in done]
    print(f"{len(todo)} agencies to guess", flush=True)
    found = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        for i, (siren, res) in enumerate(ex.map(guess, todo), 1):
            done[siren] = res
            found += bool(res["website"])
            if i % 25 == 0:
                OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
                print(f"{i} guessed, {found} websites verified", flush=True)
    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print(f"done: {found} websites verified of {len(todo)}")


if __name__ == "__main__":
    main()

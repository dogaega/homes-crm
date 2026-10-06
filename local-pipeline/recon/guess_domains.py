"""
Free website discovery for register agencies not searched yet: guess the
domains an agency would use from its name / sign, keep the ones that exist,
and accept a site only when its homepage shows the agency's name and
real-estate content. Results go into data/discovered_websites.json
(why = "guessed domain"), ready for recon/onboard.py.

    python recon/guess_domains.py
    python recon/guess_domains.py --round2   # .immo / .net / name+town forms, for agencies still without a site
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from find_websites import DATA, MANAGERS, NETWORKS, OUT, candidates, norm, tokens  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sync.netwait import online, wait_for_network  # noqa: E402

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


def hosts_round2(name: str, commune: str) -> list[str]:
    """Second-round forms: the .immo TLD many agencies use, .net, and the
    agency name followed by its town ("agence-x-nice.fr")."""
    base = slugs(name)[:3]
    town = "-".join(re.findall(r"[a-z0-9]+", norm(commune or "")))
    out = [f"{b}.immo" for b in base] + [f"{b}.net" for b in base[:2]]
    if town:
        out += [f"{base[0]}-{town}.{t}" for t in TLDS] + [f"{base[0]}{town.replace('-', '')}.{t}" for t in TLDS] if base else []
    return [h for h in dict.fromkeys(out) if 4 <= len(h.split(".")[0]) <= 45][:10]


def sole_traders() -> list[dict]:
    """Self-employed agents trading under their own agency name (a SIRENE sign)."""
    agencyish = re.compile(r"immo|immobili|agence|real estate|estate|propert|transaction|habitat|home|house|maison|riviera|azur|villa", re.I)
    rows = json.loads((DATA / "sirene_agencies.json").read_text())
    return [r for r in rows if r.get("legal_form") == "1000" and r.get("sign") and agencyish.search(r["sign"])
            and not MANAGERS.search(r["sign"]) and not NETWORKS.search(f"{r['company']} {r['sign']}")]


def guess2(c: dict) -> tuple[str, dict | None]:
    wait_for_network()
    name = c.get("sign") or c["company"]
    for host in hosts_round2(name, c.get("commune") or ""):
        if exists(host) and verify(host, name):
            return c["siren"], {"name": name, "company": c["company"], "commune": c["commune"],
                                "website": f"https://{host}/", "why": "guessed domain round 2 (verified)", "query": None}
    if not online():
        return c["siren"], None
    return c["siren"], {"name": name, "company": c["company"], "commune": c["commune"], "website": None,
                        "why": "no guessed domain verified (round 2)", "query": None, "guessed": True, "guessed2": True}


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
    wait_for_network()  # offline, every lookup "fails": that is not an answer
    name = c.get("sign") or c["company"]
    for s in slugs(name):
        for tld in TLDS:
            host = f"{s}.{tld}"
            if exists(host) and verify(host, name):
                return c["siren"], {"name": name, "company": c["company"], "commune": c["commune"],
                                    "website": f"https://{host}/", "why": "guessed domain (verified)", "query": None}
    if not online():  # the connection dropped during this agency: try it again later
        return c["siren"], None
    return c["siren"], {"name": name, "company": c["company"], "commune": c["commune"], "website": None,
                        "why": "no guessed domain verified", "query": None, "guessed": True}


def round2() -> None:
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    found_any = set()
    for f in glob.glob(str(DATA / "discovered_*.json")):
        found_any |= {k for k, v in json.loads(Path(f).read_text()).items() if v.get("website")}
    seen, todo = set(), []
    for c in candidates() + sole_traders():
        if c["siren"] in found_any or c["siren"] in seen or (done.get(c["siren"]) or {}).get("guessed2"):
            continue
        seen.add(c["siren"])
        todo.append(c)
    print(f"{len(todo)} agencies for round 2", flush=True)
    found = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i, (siren, res) in enumerate(ex.map(guess2, todo), 1):
            if res is None:
                continue
            done[siren] = res
            found += bool(res["website"])
            if i % 25 == 0:
                OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
                print(f"{i} guessed, {found} websites verified", flush=True)
    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print(f"done: {found} websites verified of {len(todo)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round2", action="store_true")
    if ap.parse_args().round2:
        round2()
        return
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    # Also redo agencies whose guesses ran while the internet was down.
    todo = [c for c in candidates() if c["siren"] not in done
            or (done[c["siren"]].get("guessed") and not done[c["siren"]]["website"])]
    print(f"{len(todo)} agencies to guess", flush=True)
    found = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        for i, (siren, res) in enumerate(ex.map(guess, todo), 1):
            if res is None:
                continue
            done[siren] = res
            found += bool(res["website"])
            if i % 25 == 0:
                OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
                print(f"{i} guessed, {found} websites verified", flush=True)
    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print(f"done: {found} websites verified of {len(todo)}")


if __name__ == "__main__":
    main()

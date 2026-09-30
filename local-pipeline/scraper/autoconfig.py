"""
Draft a generic-scraper config for every reachable agency website and
score it on 2 sampled listings → data/site_configs.json.

    python -m scraper.autoconfig [--name substr ...] [--workers 6]

Per site: index pages (sale/rent, Monaco) from recon + home links →
dominant listing-URL shape → regex → sample 2 details through
scraper.generic.parse_detail → quality score. Nothing is pushed anywhere.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper import generic  # noqa: E402
from scraper.fetch import PoliteFetcher  # noqa: E402

log = logging.getLogger("autoconfig")
DATA = Path(__file__).resolve().parents[1] / "data"

SALE = re.compile(r"vente|ventes|acheter|achat|buy|sale|sales|for-sale|vendita|kaufen|продаж", re.I)
RENT = re.compile(r"location|locations|louer|rent|rental|rentals|to-let|affitt|mieten|аренд", re.I)
NOT_INDEX = re.compile(r"gestion|management|estimation|estimate|valuation|guide|conseil|contact|about|qui-sommes|"
                       r"equipe|team|blog|news|actualit|article|mentions|legal|privacy|cookie|recrut|career|"
                       r"services|syndic|saisonni|seasonal|\.pdf|\.jpg|\.png|wp-content|/tag/|/category/|login|"
                       r"favorites|favoris|alert|compare|wishlist|/feed", re.I)
OUTSIDE = re.compile(r"\b(france|italie|italy|italia|cap[- ]d.?ail|roquebrune|menton|beausoleil|eze|[eè]ze|"
                     r"villefranche|nice|cannes|antibes|saint[- ]jean|st[- ]jean|la[- ]turbie|peille|"
                     r"cap[- ]martin|sanremo|bordighera|ventimiglia|london|dubai|miami|suisse|switzerland|"
                     r"gen[eè]ve|courchevel|gstaad|marbella|spain|espagne)\b", re.I)


def shape(url: str) -> str:
    """/fr/ventes/111775-auteuil-appartements/ → /fr/ventes/{id-slug}/"""
    parts = []
    for seg in urlparse(url).path.strip("/").split("/"):
        if re.fullmatch(r"\d{3,}", seg):
            parts.append("{id}")
        elif re.match(r"^\d{3,}[-_]", seg) or re.search(r"[-_]\d{3,}$", seg):
            parts.append("{id-slug}")
        elif re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+){3,}", seg) and not NOT_INDEX.search(seg):
            parts.append("{slug}")
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


def shape_regex(host: str, sh: str) -> str:
    rx = re.escape(sh).replace(re.escape("{id-slug}"), r"(?:\d{3,}[-_][^/?#]+|[^/?#]+[-_]\d{3,})")
    rx = rx.replace(re.escape("{id}"), r"\d{3,}").replace(re.escape("{slug}"), r"[a-z0-9]+(?:-[a-z0-9]+){3,}")
    return r"^https?://(?:www\.)?" + re.escape(host.removeprefix("www.")) + rx + r"/?(?:[?#].*)?$"


def same_host(u: str, site: str) -> bool:
    return urlparse(u).netloc.removeprefix("www.") == urlparse(site).netloc.removeprefix("www.")


def links(html: str, base: str, site: str) -> list[str]:
    out = []
    for h in re.findall(r'href="([^"#]+)"', html):
        u = urljoin(base, h.strip())
        if u.startswith("http") and same_host(u, site) and u not in out:
            out.append(u)
    return out


def score(d: dict) -> tuple[int, list[str]]:
    checks = {
        "price": d.get("price") is not None or d.get("price_on_request"),
        "transaction": d.get("transaction_type") in ("sale", "rent"),
        "area": bool(d.get("living_area_sqm")),
        "rooms": d.get("rooms") is not None or d.get("bedrooms") is not None,
        "photos": len(d.get("photo_urls") or []) >= 2,
        "ref": bool(d.get("external_ref")),
        "desc": bool(d.get("description")) and len(d.get("description") or "") > 80,
        "agent": bool(d.get("agent_name")),
    }
    return sum(checks.values()), [k for k, v in checks.items() if not v]


def build(site_recon: dict, agency: dict) -> dict:
    site = site_recon["website"]
    host = urlparse(site).netloc
    f = PoliteFetcher(delay=(1.5, 3), block_wait=10, retries=1, timeout=25)
    cfg = {"site_key": "web-" + re.sub(r"[^a-z0-9]+", "-", host.removeprefix("www.").lower()).strip("-")[:50],
           "agency": agency["name"], "website": site, "runner": "server", "status": "draft",
           "immotoolbox": "immotoolbox" in (site_recon.get("platforms") or [])}
    try:
        home = f.get(site)
    except Exception as e:
        cfg.update(status="unreachable", error=str(e)[:200])
        return cfg
    cand = [u for u in dict.fromkeys((site_recon.get("index_candidates") or []) + links(home, site, site))
            if not NOT_INDEX.search(urlparse(u).path) and not OUTSIDE.search(u) and (SALE.search(u) or RENT.search(u))]
    # Shortest paths first: "/fr/ventes" beats "/fr/ventes/monaco/t-1-appartement".
    cand.sort(key=lambda u: (len(urlparse(u).path), u))
    index = {"sale": [], "rent": []}
    for u in cand:
        kind = "rent" if RENT.search(urlparse(u).path) else "sale"
        if len(index[kind]) < 2 and not any(urlparse(u).path.startswith(urlparse(x).path.rstrip("/") + "/") for x in index[kind]):
            index[kind].append(u)
    pages = index["sale"] + index["rent"] or [site]
    shapes: Counter = Counter()
    examples: dict[str, list[str]] = {}
    found_on: dict[str, list[str]] = {"sale": [], "rent": []}
    for p in pages[:4]:
        kind = "rent" if p in index["rent"] else "sale"
        try:
            h = home if p == site else f.get(p)
        except Exception:
            continue
        for u in links(h, p, site):
            sh = shape(u)
            if "{" in sh and not NOT_INDEX.search(urlparse(u).path):
                shapes[sh] += 1
                examples.setdefault(sh, []).append(u)
                if p != site:
                    found_on[kind].append(u)
    if not shapes:
        cfg.update(status="no_listing_links", index_urls=index)
        return cfg
    sh, n = shapes.most_common(1)[0]
    cfg.update(index_urls={k: v for k, v in index.items() if v} or {"sale": [site]},
               listing_pattern=shape_regex(host, sh), listing_shape=sh, links_seen=n)
    samples = [u for u in examples[sh] if not OUTSIDE.search(u)][:2] or examples[sh][:2]
    hints = {u: k for k, v in found_on.items() for u in v}
    results = []
    for u in samples:
        try:
            d = generic.parse_detail(f.get(u), u, cfg, agency, hint=hints.get(u))
            s, missing = score(d)
            results.append({"url": u, "score": s, "missing": missing,
                            "sample": {k: d.get(k) for k in ("transaction_type", "price", "living_area_sqm", "rooms",
                                                                "bedrooms", "floor", "external_ref", "quarter",
                                                                "agent_name", "agent_phone", "agent_email")}
                            | {"photos": len(d.get("photo_urls") or [])}})
        except Exception as e:
            results.append({"url": u, "score": 0, "error": repr(e)[:150]})
    cfg["samples"] = results
    cfg["quality"] = min((r["score"] for r in results), default=0)
    cfg["status"] = "ok" if cfg["quality"] >= 6 else "weak"
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", action="append")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    agencies = {a["name"]: a for a in json.loads((DATA / "agencies.json").read_text())}
    recon = [r for r in json.loads((DATA / "website_recon.json").read_text())
             if (not args.name or any(n.lower() in r["name"].lower() for n in args.name))]
    # Duplicate agency entries can share one website: configure each site once.
    seen, todo = set(), []
    for r in recon:
        host = urlparse(r["website"]).netloc.removeprefix("www.")
        if host not in seen:
            seen.add(host)
            todo.append(r)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        cfgs = list(ex.map(lambda r: build(r, agencies.get(r["name"], {"name": r["name"]})), todo))
    path = DATA / "site_configs.json"
    existing = {c["site_key"]: c for c in json.loads(path.read_text())} if path.exists() and args.name else {}
    for c in cfgs:
        existing[c["site_key"]] = c
    path.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=1))
    print(Counter(c["status"] for c in cfgs))
    for c in sorted(cfgs, key=lambda c: -(c.get("quality") or 0)):
        miss = sorted({m for s in c.get("samples", []) for m in s.get("missing", [])})
        print(f"{c['status']:17} q={c.get('quality', '-')!s:2} {c['agency'][:26]:26} {c.get('listing_shape', '')[:40]:40} missing={miss}")


if __name__ == "__main__":
    main()

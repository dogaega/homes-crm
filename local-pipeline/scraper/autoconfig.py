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
import html as htmllib
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
# Monaco has streets named after countries (Boulevard d'Italie, de Suisse,
# de France…): "d'"/"de " right before the name means a street, not a place.
OUTSIDE = re.compile(r"\b(?<!d')(?<!d’)(?<!de )(?<!d-)(?<!de-)(france|italie|italy|italia|cap[- ]d.?ail|roquebrune|menton|beausoleil|eze|[eè]ze|"
                     r"villefranche|nice|cannes|antibes|saint[- ]jean|st[- ]jean|la[- ]turbie|peille|"
                     r"cap[- ]martin|sanremo|bordighera|ventimiglia|london|dubai|miami|suisse|switzerland|"
                     r"gen[eè]ve|courchevel|gstaad|marbella|spain|espagne)\b", re.I)


LISTING_DIR = re.compile(r"^(?:vente|ventes|location|locations|offers?|biens?|property|properties|propriete|proprietes|"
                         r"annonces?|listings?|immobilier|detail|fiche|sale|rent|buy|achat|homes?|apartments?|villas?)$", re.I)
QUERY_ID = re.compile(r"(?:^|&)((?:id|ref|bien|idbien|id_bien|annonce|id_annonce|property|prop|pid|item|itemid|code)[a-z_]*)=\d{2,}", re.I)
EXT = re.compile(r"\.(?:html?|php|aspx?)$", re.I)


def shape(url: str) -> str:
    """/fr/ventes/111775-auteuil-appartements/ → /fr/ventes/{id-slug}/
    /vente+appartement+monaco+12345.html → /{id-slug}.html
    /index.php?option=x&id=123 → /index.php?{id=}"""
    pu = urlparse(url)
    segs = pu.path.strip("/").split("/")
    parts = []
    for i, seg in enumerate(segs):
        ext = (EXT.search(seg) or [""])[0]
        core = EXT.sub("", seg)
        prev = segs[i - 1] if i else ""
        if re.fullmatch(r"\d{3,}", core):
            parts.append("{id}" + ext)
        elif re.match(r"^\d{3,}[-_+,]", core) or re.search(r"[-_+,][a-z]?\d{3,}$", core, re.I):
            parts.append("{id-slug}" + ext)
        elif re.fullmatch(r"[a-z0-9]+(?:[-+,][a-z0-9]+){3,}", core, re.I) and not NOT_INDEX.search(core):
            parts.append("{slug}" + ext)
        elif LISTING_DIR.match(prev) and re.fullmatch(r"[a-z0-9]+(?:[-+][a-z0-9]+){1,}", core, re.I) \
                and not NOT_INDEX.search(core) and i == len(segs) - 1:
            parts.append("{slug}" + ext)
        else:
            parts.append(seg)
    out = "/" + "/".join(parts)
    q = QUERY_ID.search(pu.query or "")
    if q and "{" not in out:
        out += "?{" + q.group(1).lower() + "=}"
    return out


def shape_regex(host: str, sh: str) -> str:
    path, _, qkey = sh.partition("?{")
    rx = re.escape(path)
    rx = rx.replace(re.escape("{id-slug}"), r"(?:\d{3,}[-_+,][^/?#]+?|[^/?#]+?[-_+,][a-z]?\d{3,})")
    rx = rx.replace(re.escape("{id}"), r"\d{3,}").replace(re.escape("{slug}"), r"[A-Za-z0-9]+(?:[-+,][A-Za-z0-9]+)+")
    tail = r"/?(?:[?#].*)?$"
    if qkey:
        key = qkey.rstrip("=}")
        tail = r"\?(?:[^#]*&)?" + re.escape(key) + r"=\d{2,}(?:[&#].*)?$"
    return r"^https?://(?:www\.)?" + re.escape(host.removeprefix("www.")) + rx + tail


def same_host(u: str, site: str) -> bool:
    return urlparse(u).netloc.removeprefix("www.") == urlparse(site).netloc.removeprefix("www.")


ASSET = re.compile(r"wp-json|/cache/|webmanifest|/feed/?$|\.(?:css|js|json|xml|ico|svg|png|jpe?g|webp|gif|pdf|aspx)(?:$|\?)|"
                   r"\$\{|%7B|/shop/|/product-category/", re.I)


def links(html: str, base: str, site: str) -> list[str]:
    """Real <a> links only: <link rel=manifest> etc. are not listings."""
    out = []
    for h in re.findall(r'<a\b[^>]*?\bhref=["\']([^"\'#]+)["\']', html, re.I):
        u = urljoin(base, h.strip())
        if u.startswith("http") and same_host(u, site) and u not in out:
            out.append(u)
    return out


def sitemap_urls(f: PoliteFetcher, site: str, limit: int = 8) -> list[str]:
    """URLs from /sitemap.xml (following one level of sitemap index)."""
    out: list[str] = []
    try:
        root = f.get(urljoin(site, "/sitemap.xml"))
    except Exception:
        try:
            root = f.get(urljoin(site, "/sitemap_index.xml"))
        except Exception:
            return out
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", root)
    if "<sitemapindex" in root:
        subs = [u for u in locs if re.search(r"propert|bien|annonce|listing|vente|location|sale|rent|product|produit|estate|immo", u, re.I)] or locs
        for sm in subs[:limit]:
            try:
                out += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", f.get(sm))
            except Exception:
                continue
    else:
        out = locs
    return [htmllib.unescape(u) for u in out if same_host(u, site)]


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


def build(site_recon: dict, agency: dict, render: bool = False) -> dict:
    if render:
        from scraper.browser import BrowserFetcher
        bf = BrowserFetcher(delay=(1.5, 3), block_wait=10, retries=1)
        try:
            cfg = _build(site_recon, agency, bf)
            cfg["render"] = True
            return cfg
        finally:
            bf.close()
    return _build(site_recon, agency, PoliteFetcher(delay=(1.5, 3), block_wait=10, retries=1, timeout=25))


def _build(site_recon: dict, agency: dict, f) -> dict:
    site = site_recon["website"]
    host = urlparse(site).netloc
    cfg = {"site_key": "web-" + re.sub(r"[^a-z0-9]+", "-", host.removeprefix("www.").lower()).strip("-")[:50],
           "agency": agency["name"], "website": site, "runner": "server", "status": "draft",
           "immotoolbox": "immotoolbox" in (site_recon.get("platforms") or [])}
    try:
        home = f.get(site)
    except Exception as e:
        # Reset/SSL/WAF from a datacenter IP: the Mac runner (home IP) retries it.
        cfg.update(status="unreachable", error=str(e)[:200], runner="local")
        return cfg
    cand = [u for u in dict.fromkeys((site_recon.get("index_candidates") or []) + links(home, site, site))
            if not NOT_INDEX.search(urlparse(u).path) and not OUTSIDE.search(u) and (SALE.search(u) or RENT.search(u))
            and "{" not in shape(u)]  # a listing page is not an index page
    # Shortest paths first: "/fr/ventes" beats "/fr/ventes/monaco/t-1-appartement".
    cand.sort(key=lambda u: (len(urlparse(u).path), u))
    index = {"sale": [], "rent": []}
    for u in cand:
        kind = "rent" if RENT.search(urlparse(u).path) else "sale"
        if len(index[kind]) < 2 and not any(urlparse(u).path.startswith(urlparse(x).path.rstrip("/") + "/") for x in index[kind]):
            index[kind].append(u)
    pages = index["sale"] + index["rent"] or [site]
    shapes: Counter = Counter()
    kind_shapes: dict[str, Counter] = {"sale": Counter(), "rent": Counter()}
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
            if "{" in sh and not NOT_INDEX.search(urlparse(u).path) and not ASSET.search(u):
                shapes[sh] += 1
                if p != site:
                    kind_shapes[kind][sh] += 1
                examples.setdefault(sh, []).append(u)
                if p != site:
                    found_on[kind].append(u)
    use_sitemap = False
    if not shapes or shapes.most_common(1)[0][1] < 3:
        # JS-rendered grids: the sitemap usually lists every listing URL.
        sm_urls = sitemap_urls(f, site)
        sm_shapes = Counter(shape(u) for u in sm_urls if "{" in shape(u) and not ASSET.search(u)
                            and not NOT_INDEX.search(urlparse(u).path))
        if sm_shapes and sm_shapes.most_common(1)[0][1] >= 3:
            shapes, use_sitemap = sm_shapes, True
            for u in sm_urls:
                examples.setdefault(shape(u), []).append(u)
    if not shapes or shapes.most_common(1)[0][1] < 3:
        cfg.update(status="no_listing_links", index_urls=index)
        return cfg
    sh, n = shapes.most_common(1)[0]
    # Sale and rent listings often live under different paths
    # (/produit-vente/ vs /produit-location/): cover the top shape of each.
    chosen = [sh] + [ks.most_common(1)[0][0] for ks in kind_shapes.values()
                     if ks and ks.most_common(1)[0][1] >= 2 and ks.most_common(1)[0][0] != sh]
    chosen = list(dict.fromkeys(chosen))
    cfg.update(index_urls={k: v for k, v in index.items() if v} or {"sale": [site]},
               listing_pattern="|".join(f"(?:{shape_regex(host, c)})" for c in chosen),
               listing_shape=" | ".join(chosen), links_seen=n)
    if use_sitemap:
        cfg["index_source"] = "sitemap"
    samples = [u for u in examples[sh] if not OUTSIDE.search(u)][:2] or examples[sh][:2]
    hints = {u: k for k, v in found_on.items() for u in v}
    results = []
    for u in samples:
        try:
            d = generic.parse_detail(f.get(u), u, cfg, agency, hint=hints.get(u))
            s, missing = score(d)
            results.append({"url": u, "hint": hints.get(u), "score": s, "missing": missing,
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


def rescore(cfg: dict, agency: dict) -> dict:
    """Re-parse the stored sample URLs with the current extractor."""
    if not cfg.get("samples"):
        return cfg
    f = PoliteFetcher(delay=(1.5, 3), block_wait=10, retries=1, timeout=25)
    results = []
    for smp in cfg["samples"]:
        u = smp["url"]
        try:
            d = generic.parse_detail(f.get(u), u, cfg, agency, hint=smp.get("hint"))
            sc, missing = score(d)
            results.append({"url": u, "hint": smp.get("hint"), "score": sc, "missing": missing,
                            "sample": {k: d.get(k) for k in ("transaction_type", "price", "living_area_sqm", "rooms",
                                                                "bedrooms", "floor", "external_ref", "quarter",
                                                                "agent_name", "agent_phone", "agent_email")}
                            | {"photos": len(d.get("photo_urls") or []), "cim_id": (d.get("extra") or {}).get("cim_id")}})
        except Exception as e:
            results.append({"url": u, "hint": smp.get("hint"), "score": 0, "error": repr(e)[:150]})
    cfg["samples"] = results
    cfg["quality"] = min((r["score"] for r in results), default=0)
    if cfg["status"] in ("ok", "weak"):
        cfg["status"] = "ok" if cfg["quality"] >= 6 else "weak"
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", action="append")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--redo", help="comma-separated statuses to rebuild, e.g. weak,no_listing_links")
    ap.add_argument("--rescore", action="store_true", help="re-parse stored samples only")
    ap.add_argument("--render", action="store_true", help="use headless Chromium (JS-rendered sites)")
    ap.add_argument("--new", action="store_true", help="only recon entries without a config yet (merged into site_configs.json)")
    args = ap.parse_args()
    if args.rescore:
        agencies = {a["name"]: a for a in json.loads((DATA / "agencies.json").read_text())}
        path = DATA / "site_configs.json"
        cfgs = json.loads(path.read_text())
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            cfgs = list(ex.map(lambda c: rescore(c, agencies.get(c["agency"], {"name": c["agency"]})), cfgs))
        path.write_text(json.dumps(cfgs, ensure_ascii=False, indent=1))
        print(Counter(c["status"] for c in cfgs))
        return
    if args.new:
        have = {c["agency"] for c in json.loads((DATA / "site_configs.json").read_text())}
        args.name = [r["name"] for r in json.loads((DATA / "website_recon.json").read_text()) if r["name"] not in have] or ["\0none"]
    if args.redo:
        redo = set(args.redo.split(","))
        args.name = [c["agency"] for c in json.loads((DATA / "site_configs.json").read_text()) if c["status"] in redo]
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
        cfgs = list(ex.map(lambda r: build(r, agencies.get(r["name"], {"name": r["name"]}), args.render), todo))
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

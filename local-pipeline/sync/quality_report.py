"""
Per-site data quality from a D1 sqlite file (local wrangler state or an
exported copy) → data/quality_report.json + a short printed summary.

    python sync/quality_report.py <path/to/d1.sqlite>

Flags a site when its latest run failed/blocked, when it has listings but
most lack a price, sale/rent, area or quarter, or when none of its listings
merged with any other source although the agency is on a portal.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"


def main() -> None:
    db = sqlite3.connect(sys.argv[1])
    db.row_factory = sqlite3.Row
    sites = {}
    for r in db.execute("""
        SELECT s.site_key, a.name AS agency, COUNT(*) AS n,
               SUM(s.price_at_source IS NOT NULL OR s.price_on_request = 1) AS price,
               SUM(s.transaction_type IS NOT NULL) AS tx,
               SUM(s.living_area_sqm IS NOT NULL) AS area,
               SUM(s.rooms IS NOT NULL OR s.bedrooms IS NOT NULL) AS rooms,
               SUM(s.quarter IS NOT NULL) AS quarter,
               SUM(s.coord_source IN ('listing','building')) AS located,
               SUM(s.photo_urls IS NOT NULL AND s.photo_urls != '[]') AS photos,
               SUM(s.hero_image_key IS NOT NULL) AS hero,
               SUM(s.agent_name IS NOT NULL) AS agent,
               SUM((SELECT COUNT(*) FROM property_sources o WHERE o.property_id = s.property_id) > 1) AS merged
        FROM property_sources s LEFT JOIN agencies a ON a.id = s.agency_id
        WHERE s.site_key IS NOT NULL AND s.removed_at IS NULL
        GROUP BY s.site_key"""):
        d = dict(r)
        n = d["n"] or 1
        d.update({k + "_pct": round(100 * (d[k] or 0) / n) for k in
                  ("price", "tx", "area", "rooms", "quarter", "located", "photos", "hero", "agent", "merged")})
        sites[d["site_key"]] = d
    for r in db.execute("""
        SELECT site_key, status, index_count, detail_count, error FROM scrape_runs r
        WHERE started_at = (SELECT MAX(started_at) FROM scrape_runs x WHERE x.site_key = r.site_key AND x.status != 'running')"""):
        d = sites.setdefault(r["site_key"], {"site_key": r["site_key"], "n": 0})
        d.update(last_status=r["status"], index_count=r["index_count"], error=r["error"])
    for d in sites.values():
        flags = []
        if d.get("last_status") in ("failed", "blocked"):
            flags.append(f"last run {d['last_status']}")
        if d.get("n"):
            for k, lim in (("price", 80), ("tx", 95), ("area", 60), ("quarter", 70), ("photos", 70)):
                if d.get(k + "_pct", 100) < lim:
                    flags.append(f"{k} {d[k + '_pct']}%")
            if d["n"] >= 5 and d.get("merged_pct", 0) == 0 and d["site_key"].startswith("web-"):
                flags.append("nothing merged")
        d["flags"] = flags
    out = sorted(sites.values(), key=lambda d: (-len(d["flags"]), d["site_key"]))
    (DATA / "quality_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    fam = {}
    for d in out:
        f = d["site_key"].split("-")[0]
        fam.setdefault(f, [0, 0, 0])
        fam[f][0] += 1
        fam[f][1] += d.get("n") or 0
        fam[f][2] += bool(d["flags"])
    print("family: sites / listings / flagged ->", fam)
    for d in out:
        if d["flags"]:
            print(f"  {d['site_key'][:40]:40} n={d.get('n', 0):4} {', '.join(d['flags'])}")


if __name__ == "__main__":
    main()

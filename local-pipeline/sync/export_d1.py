"""
Export the aggregator data from a local D1 (wrangler dev --local state) to
one SQL file that loads into the production D1:

    python sync/export_d1.py <path/to/local.sqlite> <out.sql>
    # then, after migration 0006 is applied remotely:
    npx wrangler d1 execute DB --remote --file <out.sql>

Only pipeline data is exported (agencies with a site, origin='pipeline'
properties, their sources, history, events, runs, health, reviews,
buildings). The local owner agent is replaced at import time by the
production database's oldest agent. INSERT OR IGNORE keeps it re-runnable.
"""

from __future__ import annotations

import sqlite3
import sys

TABLES = [
    ("buildings", "1=1"),
    ("agencies", "site_key IS NOT NULL OR cim_slug IS NOT NULL"),
    ("properties", "origin = 'pipeline'"),
    ("property_sources", "site_key IS NOT NULL"),
    ("price_history", "1=1"),
    ("listing_events", "1=1"),
    ("scrape_runs", "1=1"),
    ("site_health", "1=1"),
    ("review_queue", "1=1"),
]
OWNER = "(SELECT id FROM agents ORDER BY created_at LIMIT 1)"


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def main() -> None:
    src, out = sys.argv[1], sys.argv[2]
    db = sqlite3.connect(src)
    n = 0
    with open(out, "w") as fh:
        # properties.mandate_source_id <-> property_sources.property_id is circular;
        # defer the FK checks to commit.
        fh.write("PRAGMA defer_foreign_keys = true;\n")
        for table, where in TABLES:
            cur = db.execute(f"SELECT * FROM {table} WHERE {where}")
            cols = [d[0] for d in cur.description]
            for row in cur:
                vals = []
                for c, v in zip(cols, row):
                    vals.append(OWNER if table == "properties" and c in ("created_by", "assigned_agent_id") and v else lit(v))
                fh.write(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join(vals)});\n")
                n += 1
    print(f"{n} rows -> {out}")


if __name__ == "__main__":
    main()

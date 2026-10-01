"""
Export the aggregator data from a local D1 (wrangler dev --local state) to
one SQL file that loads into the production D1:

    python sync/export_d1.py <path/to/local.sqlite> <out.sql>
    # writes out_001.sql, out_002.sql …; after migration 0006 is applied remotely:
    for f in out_*.sql; do npx wrangler d1 execute DB --remote --file "$f"; done

Only pipeline data is exported (agencies with a site, origin='pipeline'
properties, their sources, history, events, runs, health, reviews,
buildings). The local owner agent is replaced at import time by the
production database's oldest agent. INSERT OR IGNORE keeps it re-runnable.
Hero image keys are cleared (the images are in the source environment's R2);
run `python -m scraper.daily --refresh-details` once against production to
re-upload heroes. Perceptual hashes are kept for duplicate matching.
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
    # wrangler's file splitter cuts statements at ";<newline>" even inside
    # quoted text, so only that sequence is spelled with char(10) (encoding
    # every newline would exceed SQLite's expression depth on long texts).
    text = "'" + str(v).replace("'", "''").replace("\r", "") + "'"
    return text.replace(";\n", ";' || char(10) || '")


CHUNK_ROWS = 500
CHUNK_BYTES = 700_000


def main() -> None:
    """Writes <out>_001.sql, <out>_002.sql … in dependency order; load them in order."""
    src, out = sys.argv[1], sys.argv[2]
    base = out[:-4] if out.endswith(".sql") else out
    db = sqlite3.connect(src)
    stmts: list[str] = []
    links: list[str] = []
    for table, where in TABLES:
        cur = db.execute(f"SELECT * FROM {table} WHERE {where}")
        cols = [d[0] for d in cur.description]
        for row in cur:
            vals = []
            for c, v in zip(cols, row):
                if c in ("hero_image_key", "hero_saved_at"):
                    v = None  # images live in the source R2; production re-uploads them (fingerprints kept)
                if table == "properties" and c == "merged_into" and v:
                    # Point merged parents at their target after every property exists,
                    # so each chunk loads on its own.
                    links.append(f"UPDATE properties SET merged_into = {lit(v)} WHERE id = {lit(row[cols.index('id')])};")
                    v = None
                vals.append(OWNER if table == "properties" and c in ("created_by", "assigned_agent_id") and v else lit(v))
            stmts.append(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join(vals)});")
    stmts += links
    # Files are capped by statement count and size: large files fail to
    # import (some listings carry 10 KB photo lists).
    chunks: list[list[str]] = [[]]
    size = 0
    for st in stmts:
        if chunks[-1] and (len(chunks[-1]) >= CHUNK_ROWS or size + len(st) > CHUNK_BYTES):
            chunks.append([])
            size = 0
        chunks[-1].append(st)
        size += len(st) + 1
    files = []
    for n, chunk in enumerate(chunks, 1):
        path = f"{base}_{n:03d}.sql"
        with open(path, "w") as fh:
            fh.write("\n".join(chunk) + "\n")
        files.append(path)
    print(f"{len(stmts)} statements -> {len(files)} files ({files[0]} … {files[-1]})")


if __name__ == "__main__":
    main()

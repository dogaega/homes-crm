"""
Monaco listings aggregator — Worker sync client
================================================
Thin client for the batch endpoints in worker/src/pipeline.ts, used by the
site runners (VPS and Mac). Standard library only, stores nothing on disk.

One site run:

    with SiteRun(client, "mcre", runner="server", mode="full",
                 agency={"name": "Monte-Carlo Real Estate", "website": "https://..."}) as run:
        known = run.known()                                # {url: {...}} already in D1
        run.seen([{"source_url": u, "price": p} for u, p in index])   # index sightings
        res = run.push([listing_dict, ...])                # detail records (detail=True)
        run.upload_hero(source_id, webp_bytes, phash_hex)
        run.finish("ok", index_complete=True, index_count=len(index), detail_count=n)

If the block exits with an exception the run is reported as 'failed', which
never marks listings removed. Call `run.finish("blocked", error=...)` when
the site blocked the runner.

Listing dict fields (all optional except source_url; transaction_type and
detail=True are required to create a new listing): see ListingPayload in
worker/src/pipeline.ts.

Environment:
    SYNC_API_BASE_URL   e.g. https://monaco-riviera-crm-worker.<subdomain>.workers.dev
    SYNC_API_TOKEN      the Worker's SYNC_API_TOKEN secret
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Iterable

log = logging.getLogger("worker_client")

# The Worker rejects more than 25 per request; small batches also keep each
# request well under the Workers subrequest (D1 query) limit.
BATCH_SIZE = int(os.environ.get("SYNC_BATCH_SIZE", "10"))


class WorkerError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status


class WorkerClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 60):
        self.base = (base_url or os.environ["SYNC_API_BASE_URL"]).rstrip("/")
        self.token = token or os.environ["SYNC_API_TOKEN"]
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None, *, raw: bytes | None = None,
                headers: dict[str, str] | None = None, retries: int = 6) -> Any:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        hdrs = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        if raw is None and body is not None:
            hdrs["Content-Type"] = "application/json"
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = r.read().decode()
                    return json.loads(text) if text else None
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                # 4xx is our bug or a real refusal: retrying won't help.
                if e.code < 500 or attempt == retries:
                    raise WorkerError(e.code, text) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == retries:
                    raise
                log.warning("sync %s %s failed (%s), retrying", method, path, e)
            time.sleep(2 ** (attempt + 1))

    def changelog(self, date: str | None = None) -> dict:
        return self.request("POST", "/sync/changelog", {"date": date} if date else {})

    def upsert_buildings(self, buildings: list[dict]) -> int:
        n = 0
        for i in range(0, len(buildings), 50):
            n += self.request("POST", "/sync/buildings", {"buildings": buildings[i:i + 50]})["upserted"]
        return n


class SiteRun:
    def __init__(self, client: WorkerClient, site_key: str, *, runner: str = "server",
                 mode: str = "full", agency: dict | None = None):
        self.client = client
        self.site_key = site_key
        self.runner = runner
        self.mode = mode
        self.agency = agency
        self.run_id: str | None = None
        self.finished = False
        self.stats = {"new": 0, "updated": 0, "seen": 0, "needs_detail": 0, "error": 0}

    def __enter__(self) -> "SiteRun":
        res = self.client.request("POST", "/sync/runs", {
            "site_key": self.site_key, "runner": self.runner, "mode": self.mode, "agency": self.agency,
        })
        self.run_id = res["run_id"]
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self.finished:
            status = "failed" if exc_type else "partial"
            try:
                self.finish(status, error=repr(exc) if exc else "run ended without finish()")
            except Exception:  # never let reporting mask the original error
                log.exception("could not report run end for %s", self.site_key)
        return False

    def known(self) -> dict[str, dict]:
        rows = self.client.request("GET", f"/sync/sites/{self.site_key}/known")
        return {r["source_url"]: r for r in rows}

    def push(self, listings: Iterable[dict]) -> list[dict]:
        """Send listings in batches; returns the per-listing results."""
        out: list[dict] = []
        batch: list[dict] = []
        for l in listings:
            batch.append(l)
            if len(batch) >= BATCH_SIZE:
                out += self._send(batch)
                batch = []
        if batch:
            out += self._send(batch)
        return out

    def seen(self, sightings: Iterable[dict]) -> list[str]:
        """Report index-page sightings ({source_url, price?, title?}). Returns
        the URLs the Worker does not know yet — open their detail pages."""
        res = self.push({**s, "detail": False} for s in sightings)
        return [r["source_url"] for r in res if r["outcome"] == "needs_detail"]

    def _send(self, batch: list[dict]) -> list[dict]:
        res = self.client.request("POST", f"/sync/runs/{self.run_id}/listings", {"listings": batch})["results"]
        for r in res:
            self.stats[r["outcome"]] = self.stats.get(r["outcome"], 0) + 1
            if r["outcome"] == "error":
                log.error("%s: listing %s rejected: %s", self.site_key, r["source_url"], r.get("error"))
        return res

    def upload_hero(self, source_id: str, webp: bytes, phash_hex: str | None = None) -> dict:
        headers = {"Content-Type": "image/webp"}
        if phash_hex:
            headers["X-Phash"] = phash_hex
        return self.client.request("PUT", f"/sync/sources/{source_id}/hero", raw=webp, headers=headers)

    def finish(self, status: str, *, index_complete: bool = False, index_count: int | None = None,
               detail_count: int | None = None, error: str | None = None) -> dict:
        """status: ok | partial | blocked | failed. Only 'ok' with
        index_complete=True lets the Worker count missing listings."""
        res = self.client.request("POST", f"/sync/runs/{self.run_id}/finish", {
            "status": status, "index_complete": index_complete, "index_count": index_count,
            "detail_count": detail_count, "error": error,
        })
        self.finished = True
        log.info("%s run %s: %s %s", self.site_key, self.run_id, status, self.stats)
        return res

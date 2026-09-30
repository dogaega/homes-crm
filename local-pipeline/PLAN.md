# Monaco Listings Aggregator — Plan

Handoff doc for any Claude session (local or cloud) working on the scraper
pipeline. Decisions below come from Mark, 2026-09-30. The CRM UI and the
brochure maker are separate tracks — this plan only prepares the data they
will use.

## Goal

Every property listed by the ~255 Monaco agencies (`data/monaco_agencies.csv`)
lands in the CRM daily, at high quality and detail, merged so one real
property = one record carrying every agency's listing, price and contacts.
Phase 2 (later, separate tool): all Côte d'Azur agencies.

## Decisions

| Topic | Decision |
|---|---|
| Scope | Monaco first. Sale + rent. All types: residential, new developments, commercial, parking, cellars. |
| Map | Official Monaco quarters. Exact coordinates when the listing gives them, else the building's position (building gazetteer), else quarter centroid. |
| Duplicates | Auto-merge when confident; genuinely uncertain matches go to a review queue for Mark. Nothing is ever deleted. |
| Contacts | Keep all: agency + individual agent (name, phone, email, WhatsApp). **Individual agent preferred** — extract it wherever the page shows one; agency contact is the fallback. |
| Users | Mark and his team only (internal tool to find properties for clients and contact the listing agency). |
| Priority | Coverage first, ordered by listing count; rentals equal to sales. Standard: top quality everywhere — no "good enough" fields. |
| Freshness | Full scrape every morning + light index-only check of every site several times a day, so new listings appear within hours (reach the agency before competitors). |
| "Contacted" | Manual mark by Mark per property/agency — no auto-logging. |
| Images | Hero image only stored (R2, WebP). Other photos: URLs only, loaded from source on demand. |
| Publishing | CRM is internal. Only own exclusives / mandate-agreed properties may be published — with photos we have rights to. |
| Languages | English + Russian. |
| Backend | Cloudflare D1 + R2 + the existing Worker (`worker/`). NOT Supabase. |
| Runners | Server (small VPS, ~€5–8/mo) scrapes most sites; Mark's Mac scrapes only sites that block server IPs and pushes straight to the Worker — stores nothing locally (disk is full). |
| LLM | Groq → NVIDIA → Kira free models only. Never paid OpenRouter without Mark's go-ahead. Keys are loaded at runtime from env / key files; never committed. |

## Architecture

```
 VPS runner ─┐                         ┌─ D1: properties (merged), property_sources (per agency),
             ├─ POST /sync/* (token) ─▶│      agencies, agency_contacts, price_history, photos,
 Mac runner ─┘   Cloudflare Worker     │      scrape_runs, site_health, review_queue, contact_marks,
                                       │      quarters, buildings, saved_searches
                                       └─ R2: hero images, daily changelog JSON
                                               ▲
                                   CRM (Next.js on Cloudflare) — map, filters, alerts
```

Existing pieces to build on:
- `worker/migrations/0003,0005` — agencies, agency_contacts, property_sources
  (per-agency child with price, agent, off-market 3-miss rule), properties
  (parent with map_lat/lng, living_area_sqm, mandate_source_id, pipeline_status).
- `worker/src/index.ts` `/sync/*` endpoints + `SYNC_API_TOKEN`.
- `local-pipeline/scraper/` — Camoufox orchestrator, per-site isolation, 4 archetypes.
- `local-pipeline/dedup/cluster_logic.py` — parent/child model (port its logic
  to run against D1 via the Worker; it currently targets local SQLite).
- `local-pipeline/image_pipeline/optimize_and_upload.py` — WebP + R2 upload.
  (`clean_media.py` watermark removal: internal use only, never for published listings.)

## Scrapers

- **One scraper per site**, but grouped by **platform family**: many small
  agencies run the same software (Apimo, Immotoolbox, etc.); one family parser
  + a per-site config covers them. Bespoke sites get their own parser.
- Each site config: `runner` (`server` | `local`), index URLs (sale/rent),
  pagination, card → detail link, detail field extractors, protection level.
- **Daily flow per site:** crawl index pages (all listings, cheap) → diff with
  known URLs → open detail pages only for new listings, listings whose index
  price/title changed, and a rotating ~1/7 refresh of the rest.
- **Detail page = fresh Camoufox instance with a new fingerprint**, random
  3–8 s delays, scrolling/mouse movement.
- **Blocked** (403/429/captcha/challenge): wait 30 s, retry ×3, then skip and
  log. After N daily blocks the site is flagged to move to `runner: local`.
- **LLM use:** at build time to draft configs from sample pages; at runtime
  only as a fallback extractor for fields the config missed (rate-limited).
- Never crash the run: every site and every listing is isolated; errors logged.

### Fields per listing (property_sources)
title, description, price + currency, sale/rent (+ rent period), property type,
bedrooms, rooms, living area m², terrace m², floor, parking/cellar, sea view,
building name, address, lat/lng if given, quarter, listing URL, agency
reference, agent name/phone/email, agency name/phone/email (agency name from
domain if absent), all photo URLs, first-seen / last-seen / scraped dates.

## Merging duplicates

Tiered score per candidate pair (same sale/rent):
1. exact coords (5 dp) + area ±2% → auto-merge
2. same building + area ±3% + same bedrooms (+ floor if known) → auto-merge
3. hero image perceptual hash near-match → auto-merge
4. partial matches (e.g. same quarter + area ±5% + bedrooms + price ±10%)
   → **review queue** (Mark confirms / rejects; rejection is remembered)

## Change detection & history

- New listing → event `new`. Price change → `price_history` row + `price_drop`
  / `price_rise` event.
- Removed: missing 3 consecutive days **and** the site's run succeeded those
  days (a blocked site must never mark its listings removed) → `removed`.
- Property is off-market when all its sources are removed.
- Daily changelog JSON → R2 (`changelogs/YYYY-MM-DD.json`) + run summary:
  total, new, removed, price changes, sites ok/blocked/failed, heroes saved.
- `site_health` table: per-site status, last success, listings count trend;
  a sudden drop to 0 or −50% flags the scraper as broken → repair in a Claude session.

## CRM features (data model now, UI in the CRM track)

Map by quarter + pins · filters (price, size, rooms, type, sale/rent, sea view,
parking, quarter) · per-property panel with all agencies side by side (price,
agent, contacts, link) · price history + drop alerts · days on market ·
client saved searches → alerts on new matches (Telegram/WhatsApp/email TBD) ·
manual "contacted" marks per agency · review queue · publish toggle
(exclusive/mandate only) · EN/RU.

## Recon findings (2026-09-30, `recon/recon_sites.py` → `data/site_recon.json`)

- **The agency CSV is mostly wrong: 201 of 255 domains do not exist (NXDOMAIN).**
  Names look real, URLs were guessed. Only ~54 hosts resolve (6 more resolve
  but time out: barnes-monaco.com, estator.com, faggionato.com, mercury.mc,
  nexus.mc, solamito-properties.mc — retry with a browser).
- **Authoritative agency source: Chambre Immobilière Monégasque (CIM)**,
  `chambre-immobiliere-monaco.mc/fr/agences` — 95 member agencies with manager
  name + address, each with a listings page `/fr/agence/<slug>/grid`. Its sitemap
  has ~6,900 listing-like URLs; sale/rent grids per official quarter at
  `/fr/{ventes|locations}/q_<Quarter>/grid` (grid is JS-rendered, likely Immotoolbox API).
  → First task: rebuild the agency list = CIM members + real websites of
  non-members (search each name), each with verified URL.
- **Platform families among live sites:** Immotoolbox 9 (+ CIM portal itself),
  Apimo 4, WordPress 14 (various plugins), Next.js 2, Webflow 1.
- CSV also contains **international portals** (rightmove, john-taylor,
  knightfrank, luxuryestate, jamesedition, properstar, lefigaro, hermitageriviera):
  need Monaco-only filters; mostly duplicates of agency listings — lower priority,
  useful for dedup/cross-checking.
- Protected on plain HTTP: festainvestments, jamesedition, lefigaro, phoenix.mc,
  properstar. JS-rendered: abkrealestate, ccrg.mc, meridian.mc.

## Phases

0. **Repo prep** — commit `local-pipeline/` + agency CSV, push; in the cloud
   environment set secrets (`GROQ_API_KEYS` comma-separated, `KIRA_API_KEYS`,
   `KIRA_BASE_URL`, `SYNC_API_TOKEN`) and allow full network access.
1. **Recon all 255 sites** — platform fingerprint, reachable?, protection level,
   sale/rent index URLs, approx listing count, JSON/API available?, suggested
   runner → `data/site_recon.json` + summary. Drives build order.
2. **D1 schema** — migration 0006: price_history, photos, scrape_runs,
   site_health, review_queue, contact_marks, quarters, buildings,
   saved_searches, events; extend property_sources with the fields above.
   Worker `/sync` endpoints for batch upsert + run reporting.
3. **Runner framework** — browser/fingerprint, human delays, block handling,
   incremental crawl, hero image → R2, change detection, summary, logs.
4. **Scrapers** — platform families first (biggest coverage), then bespoke
   sites, ordered by listing count.
5. **Geo + merging** — quarter polygons, Monaco building gazetteer, dedup scorer,
   review queue.
6. **Deploy** — VPS runner (systemd timer 06:00 + restart on failure); Mac
   runner (launchd 06:00, KeepAlive on crash) for `runner: local` sites.
7. **Alerts** — price drops, new matches for client saved searches.

## Status (2026-09-30, cloud session 2)

- **Phase 2 done:** `worker/migrations/0006_listings_aggregator.sql` +
  `worker/src/pipeline.ts` (batch `/sync/runs`, `/sync/runs/:id/listings|finish`,
  `/sync/sites/:key/known`, `/sync/sources/:id/hero`, `/sync/changelog`,
  `/sync/buildings`; session-auth `/pipeline/review`). Dedup tiers, 3-day
  removal rule (complete `ok` runs only; 0 or −50% index count = broken, no
  removals), site health, changelog → R2 all tested end-to-end on local D1.
  Runner client: `local-pipeline/sync/worker_client.py` (stdlib only).
- Not yet applied to the remote D1: `wrangler d1 execute DB --remote --file
  migrations/0006_listings_aggregator.sql`, then deploy. Optionally set
  `PIPELINE_AGENT_ID` (defaults to the oldest agent).
- D1/Workers Free has daily row read/write caps (enforced since 2026-09-01);
  ~7k listings with several light checks a day fits, but Workers Paid ($5/mo)
  removes the risk.
- **Backbones found (session 2):** two portals carry almost every Monaco
  listing as plain server-rendered HTML (no browser, no bot protection):
  - **CIM** `chambre-immobiliere-monaco.mc` — 95 member agencies, full fields,
    exact coords when set (default centre 43.74,7.42 = none), agency contacts.
    `scraper/cim.py`, one run per agency (`cim-<slug>`).
  - **MCRE** `montecarlo-realestate.com` — ~118 agencies incl. non-members,
    full features table + tags, agency phone, no coords. `scraper/mcre.py`
    (`mcre-<tc>`).
  - Neither shows the individual agent → per-agency site scrapers later add
    agent contacts and cover the few agencies on neither portal.
- Agency master list: `scraper/agencies.py` → `data/agencies.json` = CIM ∪ MCRE
  ∪ official directory (annuaire-monaco.mc, 118 licensed agencies). The old
  CSV is superseded (80% invented domains).
- Area semantics: CIM "Superf. totale" includes the terrace; we store living
  area (total − terrace) everywhere, total in `extra.total_area_sqm`.
- Dedup tier 0: same agency + same reference on two sites → auto-merge
  (agency identity across sites via `cim_slug`).
- No LLM needed: deterministic parsers + keyword rules (`scraper/text_rules.py`).
- Daily entry point: `python -m scraper.daily [--mode light]`.

## Open items

- Link/notes from the earlier cloud session that brainstormed the same prompt.
- Residential proxy only if a site blocks both server and Mac.
- Mac needs ~1 GB free for the local runner's Python deps (Camoufox browser
  is already cached in `~/Library/Caches/camoufox`).

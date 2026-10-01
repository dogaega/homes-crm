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
| Scope | Monaco and the Côte d'Azur (Menton → Saint-Tropez; 2026-10-01: Mark has clients in other Riviera towns). Sale + rent. All types: residential, new developments, commercial, parking, cellars. |
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

### Agency websites (night of 2026-09-30)
- The CSV's 255 names: 75 map to real agencies, 180 exist in neither the
  official directory, CIM nor MCRE (invented names / dead domains / portals)
  → `data/csv_mapping.json`. Real universe: ~185 agencies, 144 with a website.
- `recon/recon_websites.py` → platform per site (Immotoolbox 40, WordPress 37,
  custom 37, Immosoft 12, Zebrasoft 6, Apimo themes …).
- One config-driven scraper for all sites: `scraper/generic.py` (JSON-LD,
  FR/EN/IT/RU label pairs, agent cards, photos incl. JS galleries).
  Configs drafted + scored by `scraper/autoconfig.py` → `data/site_configs.json`;
  hand-written overrides in `data/site_configs_manual.json` (win per site_key).
  `daily.py --only web` runs them 6 hosts at a time; a page is stored only if
  it has a price (or on request) and a size.
- Immotoolbox-powered sites reuse CIM listing ids → exact merge (`extra.cim_id`,
  same agency only). Agency sites add listings absent from the portals
  (e.g. Petrini: 76 of 102 site listings not on CIM).
- Individual agents: most Monaco agency sites show only the agency contact;
  captured where shown.
- Not scrapable from the cloud server: SSL/WAF/reset sites have
  `runner: local` (Mac): `python -m scraper.autoconfig --redo unreachable`
  then `python -m scraper.daily --runner local` with SYNC_API_* set.
  JS-only sites (Roc, Findr, Coletti, Heritage grid, PvN, Soma …) are
  portal-only for now (`status: bad_config|no_listing_links` in configs).
- Browser: `scraper/browser.py` (headless Chromium, same interface) for
  `render: true` configs.

### Loading the data into production
Export: `data/export/monaco_listings_2026-10-01b.tar.gz` (103 ordered SQL files,
8,264 listings → 4,3xx merged properties; full test import: 0 failed files).
Migrations 0006 and 0007 are already applied to the production D1 (2026-10-01,
via the Cloudflare connector) — do not re-run them.
1. `cd worker && npx wrangler deploy`, then `npx wrangler secret put SYNC_API_TOKEN`
2. `mkdir /tmp/exp && tar -xzf ../local-pipeline/data/export/monaco_listings_2026-10-01b.tar.gz -C /tmp/exp`
   `for f in /tmp/exp/monaco_*.sql; do npx wrangler d1 execute DB --remote --file "$f" || break; done`
   (files must load in order; each is re-runnable — INSERT OR IGNORE).
3. Hero images are not in the export (they lived in the local R2):
   `SYNC_API_BASE_URL=… SYNC_API_TOKEN=… python -m scraper.daily --refresh-details`
   re-fetches details and uploads heroes. Afterwards daily runs are incremental.
4. New export later: `python sync/export_d1.py <d1.sqlite> out.sql` (size-capped
   chunks; `;`+newline inside text is encoded because wrangler splits on it).
Alternative to steps 1–3 by hand: put a Cloudflare API token (Workers Scripts,
D1, R2 edit) in the cloud environment as `CLOUDFLARE_API_TOKEN` and a session
can do all of it.

### Night 2026-09-30 → 10-01 results
- Live: 6,927 listings (CIM 2,289 · MCRE 2,351 · agency sites ~2,300) from
  166 agencies → ~3,670 properties, ~1,600 of them seen on 2+ sources.
- 242 buildings in the gazetteer; properties located by exact coordinates or
  building for ~2/3, quarter centroid for the rest.
- Review queue: ~1,100 pairs (one candidate per listing, weak signals dropped).
- Lessons: wrangler dev leaks memory (watchdog with health checks needed for
  long local runs); the cloud container is reclaimed when the session idles;
  ~40 agency sites share one Immotoolbox server — run them serially (done);
  merges must be checked against every listing of a group (floors, price
  range), not the summary row (done; /sync/split repairs old merges).
- Not covered by own-site scrapers: JS-only sites, the shared-server sites that
  started returning 500 after repeated crawling (CM Monaco, Wolzok, Gramaglia,
  Exclusive Estate, Town & Sea, Rey & Nouvion — all CIM members, covered by
  the portal), SSL/WAF sites (`runner: local`, Mac).

### Day 2 (2026-10-01)
- Agency websites: every config hand-checked with `scraper/trysite.py` (dry run:
  index + sample details, nothing pushed). `site_configs_manual.json` now holds
  the verified configs plus 25 sites confirmed to have no Monaco listings
  online (dead/parked/brochure-only/duplicate domains; agencelephare.com is a
  Normandy agency of the same name — its listings were retired via
  `/sync/sites/:key/retire`). Result: 102 agency sites produce listings
  (was 75), 3,573 listings (was ~2,300). Live total 8,200 → 4,295 properties.
- Still not covered by own-site scrapers (portals cover them where they are
  CIM/MCRE members): Astral and the WAF/403 sites (`runner: local`, Mac);
  needs_code: card-only pages without detail pages (17 Keys, Bureau d'Affaires),
  PVN (Vue app on a JSON API, mostly Côte d'Azur), Elite (broken links on the
  site itself), Vallat (1 Monaco listing, no price shown). Balkin and Lux Home
  are done (see below).
- Duplicates: gallery matching. Each listing's first 6 photos are fingerprinted
  (dHash, `photo_phashes`, migration 0007). At ingest a would-be review is
  settled: another agency sharing ≥3 photos (or ≥2 covering half the smaller
  gallery) joins the property (tier `photos`); the same agency relisting with
  no shared photo is not queued; one agency's shared photos (new-development
  renders) still go to a person. `sync/photo_review.py` applies the same rule
  to the existing queue (settled 77 of 1,099; the rest have no photo overlap
  across agencies — different photographers or different flats — or no photos).
- New generic options: `remove` (CSS blocks to drop), `overrides.transaction`,
  `transaction` pin, `accept_404`, onclick/data-href card links; parking spaces
  and cellars pass the listing gate without a size.
- Scope widened to the Côte d'Azur. Agency-site listings carry `extra.city`
  (`daily.city_of`: earliest Monaco/commune name in quarter/address/building/
  title/URL, then description; "view on X", "near X" ignored). `OUTSIDE` no
  longer drops Riviera towns — only places outside the region (`ABROAD`).
  `require_monaco` → `require_riviera` (mixed agencies drop listings that name
  no Monaco/Riviera place). Worker: non-Monaco listings skip the Monaco
  quarters/gazetteer (listing coords only, Riviera box), parents get the real
  city, and matching never crosses cities. Portals (CIM/MCRE) stay Monaco.
  Not yet widened: Kretz (index /fr/monaco/), Savills (Monaco office only),
  PVN and other Riviera-only agencies have no configs.
- Scope (Mark, 2026-10-02): Monaco all; Beausoleil + Roquebrune-Cap-Martin
  sales ≥ €500k; Menton→Nice coast, Cap Ferrat, Cannes, Antibes, Saint-Tropez
  (+ Ramatuelle, Gassin) sales ≥ €1M; rentals outside Monaco ≥ €3k/month; no
  holiday lets. `daily.SCOPE_MIN_SALE` / `in_scope`. Rejected pages go to a
  14-day skip list (`~/.cache/monaco-listings/skipped.json`) so the daily run
  doesn't re-fetch them.
- Riviera networks added: Michaël Zingraf (region pages), Côte d'Azur
  Sotheby's (sitemap, postcode-filtered), Carlton International (sitemap,
  `skip_if` for holiday lets). Next candidates: John Taylor (sitemap is
  country/area pages), Knight Frank FR, Engel & Völkers, Coldwell Banker
  (sitemap .gz, 406 to plain requests), Barnes Côte d'Azur (timeout). Le Figaro
  Propriétés, Lux-Residence, Belles Demeures answer 403.
- Runner: Mark's Mac (decision 2026-10-02). launchd: `deploy/mac/` — full run
  06:00, light 10/13/16/19, Mac-local time; skips while another run is going.
- CRM pages (2026-10-02): /listings (filters, detail with every agency's
  listing, contact marks), /requests (WORK FRANCE sheet "Покупка" tab via the
  worker secret REQUESTS_SHEET_CSV_URL, re-read at most every 10 min, plus
  manual requests in saved_searches; live matching in worker/src/crm.ts).
- Deploying: worker — `cd worker && npx wrangler deploy --config wrangler.toml`
  (without --config, wrangler 3 picks the root wrangler.jsonc = the front-end!).
  Front-end — Node 24: `WORKER_URL=https://monaco-riviera-crm-worker.markmirimsky-705.workers.dev
  npx opennextjs-cloudflare build && node_modules/.bin/wrangler deploy --config wrangler.jsonc`
  (root wrangler 4; wrangler 3 deploys a bundle that 500s on Sentry's
  process.versions check; `opennextjs-cloudflare deploy` needs R2 scope).
- Balkin (20 listings) and Lux Home (4 Monaco listings) configured and verified.
  New option `overrides.photos` (gallery container, read before page chrome is
  stripped); a configured price element without digits means price on request.

## Open items

- Link/notes from the earlier cloud session that brainstormed the same prompt.
- Residential proxy only if a site blocks both server and Mac.
- Mac needs ~1 GB free for the local runner's Python deps (Camoufox browser
  is already cached in `~/Library/Caches/camoufox`).

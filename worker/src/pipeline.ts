// Listings aggregator: batch sync from the scraper runners, change
// detection, duplicate matching and the review queue.
// See local-pipeline/PLAN.md and migrations/0006_listings_aggregator.sql.
//
// Flow per site run (runner side, local-pipeline/sync/worker_client.py):
//   POST /sync/runs                 → run_id
//   GET  /sync/sites/:key/known     → known URLs + prices (decide which details to fetch)
//   POST /sync/runs/:id/listings    → batches of index sightings and/or detail records
//   PUT  /sync/sources/:id/hero     → hero WebP to R2 (+ perceptual hash)
//   POST /sync/runs/:id/finish      → removal bookkeeping + site health
//   POST /sync/changelog            → daily changelog JSON to R2 (once, after all sites)
//
// Nothing is ever deleted. A source that stops appearing is only marked
// removed after 3 calendar days on which its site's index crawl finished
// completely; a blocked/failed/partial run never counts as a miss.

import type { Env } from './index'
import { handleCrm } from './crm'

type Json = Record<string, unknown>
type Respond = (data: unknown, status?: number) => Response

// ── pure helpers (no DB) ─────────────────────────────────────────────────

export function stripAccents(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '')
}

const BUILDING_STOPWORDS = new Set(['le', 'la', 'les', 'l', 'the', 'residence', 'immeuble', 'palais', 'building'])

// "Le Château Périgord II" and "chateau perigord 2" normalize to the same key.
export function normalizeBuildingName(name: string | null | undefined): string {
  if (!name) return ''
  const roman: Record<string, string> = { i: '1', ii: '2', iii: '3', iv: '4', v: '5' }
  const words = stripAccents(name).toLowerCase().replace(/['’]/g, ' ').replace(/[^a-z0-9 ]+/g, ' ').split(/\s+/)
  return words.filter(w => w && !BUILDING_STOPWORDS.has(w)).map(w => roman[w] ?? w).join(' ')
}

export interface QuarterRow { id: string; aliases: string; centroid_lat: number | null; centroid_lng: number | null }

// Free text ("Carré d'Or, Monte-Carlo", "q_La_Rousse") → quarters.id.
// Longest alias wins so "la rousse/saint roman" beats "rousse".
export function resolveQuarter(text: string | null | undefined, quarters: QuarterRow[]): string | null {
  if (!text) return null
  const t = ' ' + stripAccents(text).toLowerCase().replace(/[’‘`´]/g, "'").replace(/^q_/, '').replace(/[_–—-]/g, ' ').replace(/\s+/g, ' ') + ' '
  let best: { id: string; len: number } | null = null
  for (const q of quarters) {
    if (t.trim() === q.id.replace(/-/g, ' ')) return q.id
    for (const raw of JSON.parse(q.aliases) as string[]) {
      const a = stripAccents(raw).toLowerCase().replace(/[’‘`´]/g, "'").replace(/[_–—-]/g, ' ')
      if (t.includes(' ' + a + ' ') || t.includes(' ' + a + ',') || t.includes(' ' + a + '/')) {
        if (!best || a.length > best.len) best = { id: q.id, len: a.length }
      }
    }
  }
  return best?.id ?? null
}

// Monaco bounding box — sites often emit 0,0 or their French office coords.
export function inMonaco(lat: unknown, lng: unknown): boolean {
  return typeof lat === 'number' && typeof lng === 'number' && lat > 43.72 && lat < 43.76 && lng > 7.40 && lng < 7.45
}

// Côte d'Azur (Saint-Tropez → Menton) bounding box, for listings outside Monaco.
export function inRiviera(lat: unknown, lng: unknown): boolean {
  // Alpes-Maritimes + Var (Bandol to Menton, up to the Mercantour)
  return typeof lat === 'number' && typeof lng === 'number' && lat > 42.95 && lat < 44.4 && lng > 5.65 && lng < 7.72
}

// Runners send the commune in extra.city; portal listings and older runners are Monaco.
export function listingCity(l: { extra?: Json }): string {
  const c = (l.extra as Record<string, unknown> | undefined)?.city
  return typeof c === 'string' && c.trim() ? c.trim() : 'Monaco'
}

// Town named in a listing's own text, for rows stored before runners sent
// extra.city. Monaco words win only when they come first.
const TOWN_WORDS: [RegExp, string][] = [
  [/\b(?:monaco|monte[- ]?carlo|fontvieille|condamine|larvotto|moneghetti|carr[ée] d.or|mareterra|98000)\b/i, 'Monaco'],
  [/\bbeausoleil\b/i, 'Beausoleil'], [/\broquebrune|cap[- ]martin\b/i, 'Roquebrune-Cap-Martin'], [/\bmenton\b/i, 'Menton'],
  [/\bcap[- ]d.?ail\b/i, "Cap-d'Ail"], [/\bla[- ]turbie\b/i, 'La Turbie'], [/\b[eè]ze\b/i, 'Èze'],
  [/\bbeaulieu\b/i, 'Beaulieu-sur-Mer'], [/\bvillefranche\b/i, 'Villefranche-sur-Mer'], [/cap[- ]ferrat/i, 'Saint-Jean-Cap-Ferrat'],
  [/\b(?-i:Nice)\b(?!\s+[a-z0-9])/, 'Nice'], [/\bcannes\b/i, 'Cannes'], [/\bantibes|juan[- ]les[- ]pins\b/i, 'Antibes'],
  [/(?:saint|st)[- ]tropez/i, 'Saint-Tropez'], [/\bramatuelle\b/i, 'Ramatuelle'], [/\bgassin\b/i, 'Gassin'],
  [/\bbiot\b/i, 'Biot'], [/\bmougins\b/i, 'Mougins'], [/\bvalbonne\b/i, 'Valbonne'], [/\bgrasse\b/i, 'Grasse'],
  [/\bvence\b/i, 'Vence'], [/\bcagnes\b/i, 'Cagnes-sur-Mer'], [/\bmandelieu\b/i, 'Mandelieu-la-Napoule'],
  [/\bth[ée]oule\b/i, 'Théoule-sur-Mer'], [/\bgrimaud\b/i, 'Grimaud'], [/\bvallauris|golfe[- ]juan\b/i, 'Vallauris'],
  [/\ble cannet\b/i, 'Le Cannet'], [/\bsainte?[- ]maxime\b/i, 'Sainte-Maxime'], [/\bfr[ée]jus\b/i, 'Fréjus'],
  [/\bsaint[- ]rapha[eë]l\b/i, 'Saint-Raphaël'], [/\bpeille\b/i, 'Peille'], [/\bgorbio\b/i, 'Gorbio'],
]
export function townInText(title: string | null, description: string | null): string | null {
  for (const text of [title || '', (description || '').slice(0, 600)]) {
    const t = text.replace(/\b(?:rue|avenue|boulevard|bd|route|chemin|promenade)\s+(?:de |du |des |d'|d’)?[A-ZÀ-Ý][\w'’-]*/gi, ' ')
      .replace(/\b(?:vues?|views?|close to|near|proche d[eu']?|minutes? (?:from|de))\s+(?:on |over |of |sur |de |du |the |la |le )*[\w'’-]+/gi, ' ')
    let best: { i: number; town: string } | null = null
    for (const [rx, town] of TOWN_WORDS) {
      const m = t.match(rx)
      if (m && m.index != null && (!best || m.index < best.i)) best = { i: m.index, town }
    }
    if (best) return best.town
  }
  return null
}

// Same for a stored property_sources row (extra is a JSON string).
function sourceCity(extra: string | null): string {
  try { return listingCity({ extra: JSON.parse(extra || '{}') }) } catch { return 'Monaco' }
}

export function round5(n: number): number {
  return Math.round(n * 1e5) / 1e5
}

export function hamming64(a: string, b: string): number {
  if (!/^[0-9a-f]{16}$/i.test(a) || !/^[0-9a-f]{16}$/i.test(b)) return 64
  let x = BigInt('0x' + a) ^ BigInt('0x' + b)
  let n = 0
  while (x) { n += Number(x & 1n); x >>= 1n }
  return n
}

function within(a: number | null | undefined, b: number | null | undefined, pct: number): boolean {
  if (a == null || b == null || a <= 0 || b <= 0) return false
  return Math.abs(a - b) / Math.max(a, b) <= pct
}

// The fields both sides of a match comparison expose. For the incoming
// listing they come from the payload; for a candidate, from its parent row.
export interface MatchSide {
  lat: number | null
  lng: number | null
  coord_source: string | null
  area: number | null
  bedrooms: number | null
  floor: number | null
  building_id: string | null
  building_norm: string
  quarter: string | null
  price: number | null
}

export type MatchTier = 'ref' | 'coords' | 'building' | 'photos' | 'town' | 'review'
export interface MatchResult { tier: MatchTier; score: number; reasons: string[] }

// PLAN.md "Merging duplicates" tiers 1, 2 and 4 (tier 3, hero phash, runs
// when the hero is uploaded). Returns null when the pair is not plausible.
export function scoreMatch(a: MatchSide, b: MatchSide): MatchResult | null {
  const reasons: string[] = []
  const bedsKnown = a.bedrooms != null && b.bedrooms != null
  const bedsEqual = bedsKnown && a.bedrooms === b.bedrooms
  if (bedsKnown && !bedsEqual) return null
  const floorsKnown = a.floor != null && b.floor != null
  if (floorsKnown && a.floor !== b.floor) return null
  // Two units in one building often share size and layout; a price gap
  // over 10% means "maybe", never an automatic merge.
  const pricesDiffer = a.price != null && b.price != null && !within(a.price, b.price, 0.10)

  // Tier 1: exact coordinates published by both listings + area ±2%.
  if (a.coord_source === 'listing' && b.coord_source === 'listing' && a.lat != null && b.lat != null &&
      round5(a.lat) === round5(b.lat) && round5(a.lng!) === round5(b.lng!) && within(a.area, b.area, 0.02)) {
    const reasons = ['exact_coords', 'area_2pct', ...(bedsEqual ? ['bedrooms'] : [])]
    return pricesDiffer ? { tier: 'review', score: 0.6, reasons: [...reasons, 'price_differs'] } : { tier: 'coords', score: 1, reasons }
  }

  const sameBuilding = (a.building_id != null && a.building_id === b.building_id) ||
    (a.building_norm !== '' && a.building_norm === b.building_norm)
  // Both name a building and they differ: two different flats.
  if (!sameBuilding && a.building_norm !== '' && b.building_norm !== '') return null

  // Tier 2: same building + area ±3% + same bedrooms (+ floor when both known).
  if (sameBuilding && bedsEqual && within(a.area, b.area, 0.03)) {
    const reasons = ['same_building', 'area_3pct', 'bedrooms', ...(floorsKnown ? ['floor'] : [])]
    return pricesDiffer ? { tier: 'review', score: 0.6, reasons: [...reasons, 'price_differs'] } : { tier: 'building', score: 0.95, reasons }
  }
  // Many agencies don't state bedrooms: same building with area ±3% and
  // price ±3% is the same flat; with a wider price gap a person decides.
  if (sameBuilding && !bedsKnown && within(a.area, b.area, 0.03)) {
    const reasons = ['same_building', 'area_3pct', 'bedrooms_unknown', ...(floorsKnown ? ['floor'] : [])]
    return within(a.price, b.price, 0.03) ? { tier: 'building', score: 0.9, reasons: [...reasons, 'price_3pct'] }
      : { tier: 'review', score: 0.55, reasons }
  }

  // Tier 4: partial match → review queue.
  const sameQuarter = a.quarter != null && a.quarter === b.quarter
  if (!(sameBuilding || sameQuarter) || !bedsEqual) return null
  reasons.push(sameBuilding ? 'same_building' : 'same_quarter', 'bedrooms')
  let score = sameBuilding ? 0.6 : 0.4
  if (within(a.area, b.area, 0.05)) { reasons.push('area_5pct'); score += 0.2 }
  else if (a.area != null && b.area != null) return null
  const priceKnown = a.price != null && b.price != null
  if (priceKnown && within(a.price, b.price, 0.10)) { reasons.push('price_10pct'); score += 0.15 }
  else if (priceKnown) return null
  if (floorsKnown) { reasons.push('floor'); score += 0.05 }
  // A same-quarter guess needs both area and price to agree.
  if (!sameBuilding && !(reasons.includes('area_5pct') && reasons.includes('price_10pct'))) return null
  return { tier: 'review', score: Math.min(score, 0.9), reasons }
}

// ── payload ──────────────────────────────────────────────────────────────

export interface ListingPayload {
  source_url: string
  detail?: boolean                  // true when fields come from the detail page
  transaction_type?: 'sale' | 'rent'
  price?: number | null
  currency?: string
  price_on_request?: boolean
  rent_period?: 'month' | 'week' | 'year' | 'season'
  title?: string
  description?: string
  property_type?: string
  bedrooms?: number
  rooms?: number
  bathrooms?: number
  living_area_sqm?: number
  terrace_sqm?: number
  floor?: number
  parking?: number
  cellar?: boolean
  sea_view?: boolean
  building_name?: string
  address?: string
  quarter?: string                  // free text; resolved to quarters.id
  lat?: number
  lng?: number
  external_ref?: string
  agent_name?: string
  agent_phone?: string
  agent_email?: string
  agent_whatsapp?: string
  agency_phone?: string
  agency_email?: string
  photo_urls?: string[]
  photo_phashes?: string[]           // dHash hex of the first photos (gallery matching)
  extra?: Json
}

// ── request-scoped context ───────────────────────────────────────────────

interface BuildingRow { id: string; normalized_name: string; aliases: string; quarter: string | null; lat: number | null; lng: number | null }

const GENERIC_KEY = 'config/generic_phashes.json'

class Ctx {
  private generic?: string[]
  private quarters?: QuarterRow[]
  private buildings?: Map<string, BuildingRow>
  constructor(readonly env: Env, readonly now: string) {}
  get today() { return this.now.slice(0, 10) }
  get db() { return this.env.DB }

  // Logos, agent portraits, stock views, "confidential" stamps: images seen
  // on many different properties (rebuilt by POST /sync/generic-photos).
  async genericPhashes(): Promise<string[]> {
    if (!this.generic) {
      const obj = await this.env.DOCS.get(GENERIC_KEY)
      this.generic = obj ? (await obj.json<string[]>()) : []
    }
    return this.generic
  }
  async isGeneric(h: string | null | undefined): Promise<boolean> {
    if (!h) return false
    return (await this.genericPhashes()).some(g => hamming64(g, h) <= PHASH_NEAR)
  }
  async realPhotos(hashes: string[]): Promise<string[]> {
    const g = await this.genericPhashes()
    return hashes.filter(h => !g.some(x => hamming64(x, h) <= PHASH_NEAR))
  }

  async getQuarters(): Promise<QuarterRow[]> {
    if (!this.quarters) {
      const { results } = await this.db.prepare('SELECT id, aliases, centroid_lat, centroid_lng FROM quarters').all<QuarterRow>()
      this.quarters = results || []
    }
    return this.quarters
  }

  async findBuilding(name: string | undefined): Promise<BuildingRow | null> {
    const norm = normalizeBuildingName(name)
    if (!norm) return null
    if (!this.buildings) {
      const { results } = await this.db.prepare('SELECT id, normalized_name, aliases, quarter, lat, lng FROM buildings').all<BuildingRow>()
      this.buildings = new Map()
      for (const b of results || []) {
        this.buildings.set(b.normalized_name, b)
        for (const a of JSON.parse(b.aliases || '[]') as string[]) this.buildings.set(normalizeBuildingName(a), b)
      }
    }
    return this.buildings.get(norm) ?? null
  }

  async event(type: string, sourceId: string | null, propertyId: string | null, siteKey: string | null, runId: string | null, data: Json = {}) {
    await this.db.prepare(
      `INSERT INTO listing_events (id, type, source_id, property_id, site_key, run_id, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(crypto.randomUUID(), type, sourceId, propertyId, siteKey, runId, JSON.stringify(data), this.now).run()
  }
}

async function pipelineAgentId(env: Env & { PIPELINE_AGENT_ID?: string }): Promise<string> {
  if (env.PIPELINE_AGENT_ID) return env.PIPELINE_AGENT_ID
  const row = await env.DB.prepare('SELECT id FROM agents ORDER BY created_at LIMIT 1').first<{ id: string }>()
  if (!row) throw new Error('No agent exists to own pipeline properties; set PIPELINE_AGENT_ID')
  return row.id
}

// ── parent property maintenance ──────────────────────────────────────────

const COORD_RANK: Record<string, number> = { listing: 3, building: 2, quarter: 1 }

// Re-derive the parent's summary from its live sources: lowest price,
// best-known coordinates, first non-null of each field (portal listings first — their fields are structured — then newest detail).
// When no source is live the parent goes off-market (event emitted once).
async function refreshParent(ctx: Ctx, propertyId: string, runId: string | null) {
  const parent = await ctx.db.prepare('SELECT pipeline_status, origin FROM properties WHERE id = ?').bind(propertyId).first<any>()
  if (!parent || parent.origin !== 'pipeline') return
  const { results } = await ctx.db.prepare(
    `SELECT * FROM property_sources WHERE property_id = ? AND is_off_market = 0 AND removed_at IS NULL
     ORDER BY (site_key LIKE 'web-%') ASC, detail_scraped_at DESC`
  ).bind(propertyId).all<any>()
  const live = results || []

  if (live.length === 0) {
    if (parent.pipeline_status !== 'off_market') {
      await ctx.db.prepare(
        `UPDATE properties SET pipeline_status = 'off_market', listing_status = 'withdrawn', source_count = 0, updated_at = ? WHERE id = ?`
      ).bind(ctx.now, propertyId).run()
      await ctx.event('off_market', null, propertyId, null, runId)
    }
    return
  }

  const first = (k: string) => { for (const s of live) if (s[k] != null && s[k] !== '') return s[k]; return null }
  const priced = live.filter(s => s.price_at_source != null && !s.price_on_request)
  const price = priced.length ? Math.min(...priced.map(s => s.price_at_source)) : null
  const geo = [...live].filter(s => s.lat != null).sort((a, b) => (COORD_RANK[b.coord_source] || 0) - (COORD_RANK[a.coord_source] || 0))[0]
  const minIso = (k: string) => live.map(s => s[k]).filter(Boolean).sort()[0] ?? null
  const maxIso = (k: string) => live.map(s => s[k]).filter(Boolean).sort().pop() ?? null
  const hero = live.find(s => s.hero_image_key)?.hero_image_key ?? null

  await ctx.db.prepare(
    `UPDATE properties SET
       price = ?, property_name = COALESCE(?, property_name), description = COALESCE(?, description),
       property_type = COALESCE(?, property_type), bedrooms = COALESCE(?, bedrooms), room_count = COALESCE(?, room_count),
       bathrooms = COALESCE(?, bathrooms), living_area_sqm = COALESCE(?, living_area_sqm), terrace_sqm = COALESCE(?, terrace_sqm),
       floor = COALESCE(?, floor), parking_spaces = COALESCE(?, parking_spaces), sea_view = COALESCE(?, sea_view),
       building_id = COALESCE(?, building_id), building_name = COALESCE(?, building_name), quarter = COALESCE(?, quarter),
       district = COALESCE(?, district), address = COALESCE(?, address),
       map_lat = COALESCE(?, map_lat), map_lng = COALESCE(?, map_lng), coord_source = COALESCE(?, coord_source),
       hero_image_key = COALESCE(hero_image_key, ?), source_count = ?, first_seen_at = ?, last_seen_at = ?,
       pipeline_status = CASE WHEN pipeline_status = 'off_market' THEN 'uncontacted' ELSE pipeline_status END,
       listing_status = 'active', updated_at = ?
     WHERE id = ?`
  ).bind(
    price, first('listing_title'), first('listing_description'),
    first('property_type'), first('bedrooms'), first('rooms'),
    first('bathrooms'), first('living_area_sqm'), first('terrace_sqm'),
    first('floor'), first('parking'), first('sea_view'),
    first('building_id'), first('building_name'), first('quarter'),
    first('quarter'), first('address'),
    geo?.lat ?? null, geo?.lng ?? null, geo?.coord_source ?? null,
    hero, live.length, minIso('first_seen_at'), maxIso('last_seen_at'),
    ctx.now, propertyId,
  ).run()
}

// Random, not max+1: runners sync several sites concurrently and a
// sequential counter would hand two parents the same reference.
function pipelineRef(): string {
  return 'MKT-' + crypto.randomUUID().replace(/-/g, '').slice(0, 8).toUpperCase()
}

async function createParent(ctx: Ctx, agentId: string, transaction: string, quarter: string | null,
                            city = 'Monaco'): Promise<string> {
  const id = crypto.randomUUID()
  await ctx.db.prepare(
    `INSERT INTO properties (id, property_id, address, city, state, zip_code, created_by, origin, transaction_type, quarter,
       listing_status, pipeline_status, created_at, updated_at)
     VALUES (?, ?, ?, ?, '', ?, ?, 'pipeline', ?, ?, 'active', 'uncontacted', ?, ?)`
  ).bind(id, pipelineRef(), city, city, city === 'Monaco' ? '98000' : '', agentId, transaction, quarter, ctx.now, ctx.now).run()
  return id
}

// Move a source under another parent; an emptied parent is archived with
// merged_into set (never deleted, so links and marks keep resolving).
async function moveSource(ctx: Ctx, sourceId: string, fromId: string, toId: string, tier: string, runId: string | null) {
  await ctx.db.prepare('UPDATE property_sources SET property_id = ?, match_tier = ? WHERE id = ?').bind(toId, tier, sourceId).run()
  await ctx.db.prepare('UPDATE price_history SET property_id = ? WHERE source_id = ?').bind(toId, sourceId).run()
  await ctx.db.prepare(
    `UPDATE review_queue SET status = 'merged', decided_at = ? WHERE source_id = ? AND candidate_property_id = ? AND status = 'pending'`
  ).bind(ctx.now, sourceId, toId).run()
  const left = await ctx.db.prepare('SELECT COUNT(*) AS n FROM property_sources WHERE property_id = ?').bind(fromId).first<{ n: number }>()
  if (!left?.n) {
    await ctx.db.prepare(
      `UPDATE properties SET merged_into = ?, pipeline_status = 'archived', source_count = 0, updated_at = ? WHERE id = ?`
    ).bind(toId, ctx.now, fromId).run()
    await ctx.db.prepare('UPDATE contact_marks SET property_id = ? WHERE property_id = ?').bind(toId, fromId).run()
    // Redirect pending reviews to the surviving parent; a review that would
    // duplicate an existing pair is closed instead.
    await ctx.db.prepare(`UPDATE OR IGNORE review_queue SET candidate_property_id = ? WHERE candidate_property_id = ? AND status = 'pending'`).bind(toId, fromId).run()
    await ctx.db.prepare(`UPDATE review_queue SET status = 'merged', decided_at = ? WHERE candidate_property_id = ? AND status = 'pending'`).bind(ctx.now, fromId).run()
  } else {
    await refreshParent(ctx, fromId, runId)
  }
  await refreshParent(ctx, toId, runId)
  await ctx.event('merged', sourceId, toId, null, runId, { from_property_id: fromId, tier })
}

// ── matching ─────────────────────────────────────────────────────────────

function sideFromParent(p: any): MatchSide {
  return {
    lat: p.map_lat, lng: p.map_lng, coord_source: p.coord_source, area: p.living_area_sqm,
    bedrooms: p.bedrooms, floor: p.floor, building_id: p.building_id,
    building_norm: normalizeBuildingName(p.building_name), quarter: p.quarter, price: p.price,
  }
}

interface MatchDecision { autoTo: { id: string; tier: MatchTier } | null; review: { id: string; m: MatchResult }[] }

function isTwin(a: MatchSide, b: MatchSide, city: string): boolean {
  if (!within(a.area, b.area, 0.01) || !within(a.price, b.price, 0.01)) return false
  if (a.bedrooms != null && b.bedrooms != null && a.bedrooms !== b.bedrooms) return false
  if (a.floor != null && b.floor != null && a.floor !== b.floor) return false
  if (a.building_norm && b.building_norm && a.building_norm !== b.building_norm) return false
  const sameBuilding = !!a.building_norm && a.building_norm === b.building_norm
  if (city === 'Monaco') return sameBuilding || (a.quarter != null && a.quarter === b.quarter)
  return true  // outside Monaco both sides already carry the same town
}

function townSide(s: MatchSide, city: string): MatchSide {
  return { ...s, quarter: `town:${city}`, building_id: null, building_norm: '' }
}

async function findMatches(ctx: Ctx, side: MatchSide, transaction: string, siteKey: string, coordsSharedOnSite: boolean,
                           exclude: string | null = null, city = 'Monaco'): Promise<MatchDecision> {
  // Candidate pull is index-backed (idx_properties_origin) and bounded.
  const areaLo = side.area ? side.area * 0.95 : null
  const areaHi = side.area ? side.area * 1.05 : null
  const { results } = await ctx.db.prepare(
    `SELECT p.*, (SELECT GROUP_CONCAT(DISTINCT s.site_key) FROM property_sources s
                  WHERE s.property_id = p.id AND s.removed_at IS NULL) AS site_keys
     FROM properties p
     WHERE p.origin = 'pipeline' AND p.transaction_type = ? AND p.merged_into IS NULL AND p.id != ?
       AND COALESCE(p.city, 'Monaco') = ?
       AND (? IS NULL OR p.living_area_sqm IS NULL OR p.living_area_sqm BETWEEN ? AND ?)
       AND (? IS NULL OR p.quarter IS NULL OR p.quarter = ? OR p.building_id = ?)
     LIMIT 300`
  ).bind(transaction, exclude ?? '', city, side.area, areaLo, areaHi, side.quarter, side.quarter, side.building_id).all<any>()

  const decision: MatchDecision = { autoTo: null, review: [] }
  const scored: { id: string; m: MatchResult; sameSite: boolean }[] = []
  for (const p of results || []) {
    // Outside Monaco there are no quarters or gazetteer buildings, and "building
    // names" are mostly listing titles: the town stands in for the quarter.
    const cand = city === 'Monaco' ? sideFromParent(p) : townSide(sideFromParent(p), city)
    // A coordinate the site puts on many listings (its office, a street
    // centroid) is not evidence of identity.
    const base = city === 'Monaco' ? side : townSide(side, city)
    const mine = coordsSharedOnSite ? { ...base, coord_source: 'building' } : base
    let m = scoreMatch(mine, cand)
    const sameSite = String(p.site_keys || '').split(',').includes(siteKey)
    // Near-identical twin: same place, size and price within 1%, nothing
    // contradicting → one property, whoever lists it (agencies also post the
    // same flat twice; identical studios in one residence read as one offer).
    if (isTwin(base, cand, city)) {
      m = { tier: city === 'Monaco' ? 'building' : 'town', score: 0.92, reasons: ['twin_area_1pct_price_1pct'] }
      scored.push({ id: p.id, m, sameSite: false })
      continue
    }
    if (!m) continue
    // Outside Monaco: another agency's listing with the same bedrooms, area
    // within 1% and price within 2% is the same property.
    if (city !== 'Monaco' && m.tier === 'review' && !sameSite && mine.bedrooms != null && mine.bedrooms === cand.bedrooms &&
        within(mine.area, cand.area, 0.01) && within(mine.price, cand.price, 0.02)) {
      m = { tier: 'town', score: 0.9, reasons: [...m.reasons, 'area_1pct', 'price_2pct'] }
    }
    scored.push({ id: p.id, m, sameSite })
  }
  scored.sort((x, y) => y.m.score - x.m.score)
  const mineProfile: Profile = { floors: new Set(side.floor != null ? [side.floor] : []), minP: side.price, maxP: side.price }
  for (const s of scored) {
    if (s.m.tier !== 'review' && !compatible(mineProfile, await profileOf(ctx, s.id))) {
      s.m = { ...s.m, tier: 'review', score: Math.min(s.m.score, 0.6), reasons: [...s.m.reasons, 'group_conflict'] }
    }
    // An agency listing two identical units (new developments) must never
    // auto-collapse them — that goes to review instead.
    if (!decision.autoTo && s.m.tier !== 'review' && !s.sameSite) { decision.autoTo = { id: s.id, tier: s.m.tier }; continue }
    if (decision.autoTo || decision.review.length >= 1) continue
    const rm = s.m.tier === 'review' ? s.m : { ...s.m, tier: 'review' as MatchTier, reasons: [...s.m.reasons, 'same_agency'] }
    const cand = (results || []).find((p: any) => p.id === s.id)
    if (worthReview(rm, side.price, cand?.price ?? null, side.area, cand?.living_area_sqm ?? null)) decision.review.push({ id: s.id, m: rm })
  }
  return decision
}

// What a parent's live sources say about the flat: every known floor and
// the price range. Merges are checked against all of them, not just the
// parent's summary row, so one floor-less listing can't bridge two units.
interface Profile { floors: Set<number>; minP: number | null; maxP: number | null }

async function profileOf(ctx: Ctx, propertyId: string): Promise<Profile> {
  const { results } = await ctx.db.prepare(
    `SELECT floor, price_at_source AS p FROM property_sources WHERE property_id = ? AND removed_at IS NULL`
  ).bind(propertyId).all<{ floor: number | null; p: number | null }>()
  const floors = new Set<number>(); let minP: number | null = null, maxP: number | null = null
  for (const r of results || []) {
    if (r.floor != null) floors.add(r.floor)
    if (r.p != null && r.p > 0) { minP = minP == null ? r.p : Math.min(minP, r.p); maxP = maxP == null ? r.p : Math.max(maxP, r.p) }
  }
  return { floors, minP, maxP }
}

function compatible(a: Profile, b: Profile): boolean {
  if (a.floors.size && b.floors.size && ![...a.floors].some(f => b.floors.has(f))) return false
  if (a.floors.size > 1 || b.floors.size > 1) {
    // A group that already spans floors is suspect: only join on a shared floor set.
    if ([...a.floors].some(f => !b.floors.has(f)) && b.floors.size) return false
  }
  const lo = [a.minP, b.minP].filter((x): x is number => x != null), hi = [a.maxP, b.maxP].filter((x): x is number => x != null)
  if (lo.length === 2 && Math.max(...hi) / Math.min(...lo) > 1.12) return false
  return true
}

// Only pairs a person can usefully decide reach the review queue: Monaco has
// many look-alike flats, so weak signals are dropped rather than queued.
function worthReview(m: MatchResult, aPrice: number | null, bPrice: number | null, aArea: number | null, bArea: number | null): boolean {
  const r = new Set(m.reasons)
  const priceWithin = (pct: number) => aPrice != null && bPrice != null && within(aPrice, bPrice, pct)
  if (r.has('same_agency')) return priceWithin(0.01)          // one agency's two units: only near-identical
  if (r.has('hero_phash') && !r.has('area_5pct')) return false  // shared building photo
  if (r.has('same_quarter') && !r.has('same_building')) return within(aArea, bArea, 0.03) && priceWithin(0.05)
  return true
}

// Apply a review decision (person in the UI, or the photo comparer via
// /sync/reviews/decide, which records its evidence in the reasons).
async function decideReview(ctx: Ctx, item: any, decision: 'merge' | 'reject', agentId: string | null, evidence: string | null): Promise<Json> {
  const source = await ctx.db.prepare('SELECT id, property_id FROM property_sources WHERE id = ?').bind(item.source_id).first<any>()
  if (decision === 'merge' && source && source.property_id !== item.candidate_property_id) {
    await moveSource(ctx, source.id, source.property_id, item.candidate_property_id, 'review', null)
  }
  const reasons = evidence ? JSON.stringify([...JSON.parse(item.reasons), evidence]) : item.reasons
  await ctx.db.prepare(`UPDATE review_queue SET status = ?, decided_by = ?, decided_at = ?, reasons = ? WHERE id = ?`)
    .bind(decision === 'merge' ? 'merged' : 'rejected', agentId, ctx.now, reasons, item.id).run()
  return { id: item.id, status: decision === 'merge' ? 'merged' : 'rejected', property_id: decision === 'merge' ? item.candidate_property_id : source?.property_id }
}

// ── gallery matching ─────────────────────────────────────────────────────

const PHASH_NEAR = 4  // differing bits for "same photo" after resize/recompression

function phashList(v: unknown): string | null {
  if (!Array.isArray(v)) return null
  const ok = v.filter((h): h is string => typeof h === 'string' && /^[0-9a-f]{16}$/.test(h)).slice(0, 8)
  return ok.length ? JSON.stringify(ok) : null
}

export function sharedPhotos(mine: string[], theirs: string[]): number {
  return mine.filter(h => theirs.some(o => hamming64(h, o) <= PHASH_NEAR)).length
}

// Same rule as sync/photo_review.py: ≥3 shared photos, or ≥2 covering half
// the smaller gallery.
export function galleriesMatch(shared: number, mine: number, theirs: number): boolean {
  return shared >= 3 || (shared >= 2 && shared * 2 >= Math.min(mine, theirs))
}

// Settle a would-be review from photo galleries: another agency's listing of
// the same flat shares its photos → join it; the same agency relisting with
// none of the same photos is a different unit → no review. One agency's
// shared photos (new-development renders) still go to a person.
async function galleryDecide(ctx: Ctx, decision: MatchDecision, agencyId: string, phashesJson: string | null, side: MatchSide) {
  const mine: string[] = await ctx.realPhotos(phashesJson ? JSON.parse(phashesJson) : [])
  if (mine.length < 2) return
  const cand = decision.review[0]
  const { results } = await ctx.db.prepare(
    `SELECT agency_id, photo_phashes FROM property_sources WHERE property_id = ? AND removed_at IS NULL AND photo_phashes IS NOT NULL`
  ).bind(cand.id).all<{ agency_id: string; photo_phashes: string }>()
  const theirs = await ctx.realPhotos((results || []).flatMap(r => JSON.parse(r.photo_phashes) as string[]))
  if (theirs.length < 2) return
  const sameAgency = (results || []).some(r => r.agency_id === agencyId)
  const shared = sharedPhotos(mine, theirs)
  const mineProfile: Profile = { floors: new Set(side.floor != null ? [side.floor] : []), minP: side.price, maxP: side.price }
  if (!sameAgency && galleriesMatch(shared, mine.length, theirs.length) && compatible(mineProfile, await profileOf(ctx, cand.id))) {
    decision.autoTo = { id: cand.id, tier: 'photos' }
    decision.review = []
  } else if (sameAgency && shared === 0 && mine.length >= 3 && theirs.length >= 3) {
    decision.review = []
  }
}

async function queueReview(ctx: Ctx, sourceId: string, candidateId: string, m: MatchResult) {
  await ctx.db.prepare(
    `INSERT OR IGNORE INTO review_queue (id, source_id, candidate_property_id, score, reasons, status, created_at)
     VALUES (?, ?, ?, ?, ?, 'pending', ?)`
  ).bind(crypto.randomUUID(), sourceId, candidateId, m.score, JSON.stringify(m.reasons), ctx.now).run()
}

// ── listing ingest ───────────────────────────────────────────────────────

type Outcome = 'new' | 'updated' | 'seen' | 'needs_detail' | 'error'
interface ListingResult { source_url: string; outcome: Outcome; source_id?: string; property_id?: string; match?: string; events?: string[]; error?: string }

const b01 = (v: unknown) => (v == null ? null : v ? 1 : 0)
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null)

// Price sanity: what a site's layout makes the parser read is sometimes not
// the asking price. No Monaco/Riviera rent reaches €250k a month; nothing is
// for sale under €20k (cellars start ~€50k) — that is a monthly rent; under
// €1k it is not a price at all.
export function saneTransaction(tx: string | undefined, price: number | null): { tx: string | undefined; price: number | null } {
  if (price == null) return { tx, price }
  // Above €150M is a misread (a date or postcode glued onto the price), not a Riviera home.
  if (price > 150_000_000) return { tx, price: null }
  if (tx === 'rent' && price >= 250_000) return { tx: 'sale', price }
  if (tx === 'sale' && price < 1_000) return { tx, price: null }
  if (tx === 'sale' && price < 20_000) return { tx: 'rent', price }
  return { tx, price }
}

async function ingestListing(ctx: Ctx, run: any, agencyId: string, agentId: string, l: ListingPayload): Promise<ListingResult> {
  const url = str(l.source_url)
  if (!url) return { source_url: String(l.source_url), outcome: 'error', error: 'source_url required' }
  const events: string[] = []
  const sane = saneTransaction(l.transaction_type, l.price_on_request ? null : num(l.price))
  if (sane.tx !== l.transaction_type) l = { ...l, transaction_type: sane.tx as ListingPayload['transaction_type'], rent_period: sane.tx === 'rent' ? 'month' : undefined }
  const price = sane.price

  const existing = await ctx.db.prepare('SELECT * FROM property_sources WHERE source_url = ?').bind(url).first<any>()

  if (existing) {
    const priceChanged = price != null && existing.price_at_source != null && Math.abs(price - existing.price_at_source) >= 1
    const relisted = existing.removed_at != null || existing.is_off_market === 1
    const seenToday = (existing.last_seen_at || '').slice(0, 10) === ctx.today
    const titleChanged = str(l.title) != null && str(l.title) !== existing.listing_title

    // Index sighting with nothing new, already recorded today: no write.
    if (!l.detail && !priceChanged && !relisted && !titleChanged && seenToday) {
      return { source_url: url, outcome: 'seen', source_id: existing.id, property_id: existing.property_id }
    }

    if (priceChanged) {
      await ctx.db.prepare(
        `INSERT INTO price_history (id, source_id, property_id, price, previous_price, currency, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)`
      ).bind(crypto.randomUUID(), existing.id, existing.property_id, price, existing.price_at_source, l.currency || existing.currency, ctx.now).run()
      const type = price! < existing.price_at_source ? 'price_drop' : 'price_rise'
      await ctx.event(type, existing.id, existing.property_id, run.site_key, run.id, { from: existing.price_at_source, to: price, currency: l.currency || existing.currency })
      events.push(type)
    }
    if (relisted) {
      await ctx.event('relisted', existing.id, existing.property_id, run.site_key, run.id)
      events.push('relisted')
    }

    const sets: string[] = ['last_seen_at = ?', 'last_run_id = ?', 'missed_days = 0', 'last_missed_date = NULL',
      'removed_at = NULL', 'is_off_market = 0', 'consecutive_404_count = 0', 'site_key = COALESCE(site_key, ?)']
    const vals: unknown[] = [ctx.now, run.id, run.site_key]
    if (price != null) { sets.push('price_at_source = ?'); vals.push(price) }
    if (l.price_on_request != null) { sets.push('price_on_request = ?'); vals.push(b01(l.price_on_request)) }
    if (str(l.title)) { sets.push('listing_title = ?'); vals.push(str(l.title)) }
    if (l.detail) {
      const d = await detailColumns(ctx, l)
      for (const [k, v] of Object.entries(d)) { if (v != null) { sets.push(`${k} = ?`); vals.push(v) } }
      sets.push('detail_scraped_at = ?'); vals.push(ctx.now)
    }
    await ctx.db.prepare(`UPDATE property_sources SET ${sets.join(', ')} WHERE id = ?`).bind(...vals, existing.id).run()
    if (priceChanged || relisted || l.detail) await refreshParent(ctx, existing.property_id, run.id)
    if (priceChanged) run._price_changes++
    return { source_url: url, outcome: 'updated', source_id: existing.id, property_id: existing.property_id, events }
  }

  // Unknown URL: only a detail record can create a listing.
  if (!l.detail || (l.transaction_type !== 'sale' && l.transaction_type !== 'rent')) {
    return { source_url: url, outcome: 'needs_detail' }
  }

  const d = await detailColumns(ctx, l)
  const side: MatchSide = {
    lat: d.lat as number | null, lng: d.lng as number | null, coord_source: d.coord_source as string | null,
    area: d.living_area_sqm as number | null, bedrooms: d.bedrooms as number | null, floor: d.floor as number | null,
    building_id: d.building_id as string | null, building_norm: normalizeBuildingName(d.building_name as string | null),
    quarter: d.quarter as string | null, price,
  }
  let coordsShared = false
  if (d.coord_source === 'listing') {
    const shared = await ctx.db.prepare(
      `SELECT COUNT(*) AS n FROM property_sources WHERE site_key = ? AND lat = ? AND lng = ? AND removed_at IS NULL`
    ).bind(run.site_key, d.lat, d.lng).first<{ n: number }>()
    coordsShared = (shared?.n ?? 0) >= 3
  }
  // Tier 0: the same agency's listing already came in through another site
  // (its CIM page vs MCRE vs its own website) under the same reference.
  // Immotoolbox-powered agency sites reuse the CIM portal's listing id.
  const cimId = typeof l.extra?.cim_id === 'string' && /^\d{4,7}$/.test(l.extra.cim_id as string) ? l.extra.cim_id as string : null
  const cimMatch = cimId && agencyId ? await ctx.db.prepare(
    `SELECT property_id FROM property_sources WHERE source_url = ? AND agency_id = ? AND removed_at IS NULL`
  ).bind(`https://www.chambre-immobiliere-monaco.mc/fr/bien/${cimId}/bien`, agencyId).first<{ property_id: string }>() : null
  // …and the other way round: a CIM listing whose agency site copy came first.
  const cimUrlId = url.match(/chambre-immobiliere-monaco\.mc\/fr\/bien\/(\d+)\//)?.[1]
  const siteCopy = !cimMatch && cimUrlId && agencyId ? await ctx.db.prepare(
    `SELECT property_id FROM property_sources WHERE agency_id = ? AND extra LIKE ? AND removed_at IS NULL LIMIT 1`
  ).bind(agencyId, `%"cim_id":"${cimUrlId}"%`).first<{ property_id: string }>() : null
  const refMatch = cimMatch ?? siteCopy ?? (str(l.external_ref) && agencyId ? await ctx.db.prepare(
    `SELECT property_id FROM property_sources
     WHERE agency_id = ? AND external_ref = ? AND site_key != ? AND transaction_type = ? AND removed_at IS NULL LIMIT 1`
  ).bind(agencyId, str(l.external_ref), run.site_key, l.transaction_type).first<{ property_id: string }>() : null)
  const decision: MatchDecision = refMatch
    ? { autoTo: { id: refMatch.property_id, tier: 'ref' }, review: [] }
    : await findMatches(ctx, side, l.transaction_type, run.site_key, coordsShared, null, listingCity(l))
  if (!decision.autoTo && decision.review.length) await galleryDecide(ctx, decision, agencyId, d.photo_phashes as string | null, side)
  const propertyId = decision.autoTo?.id ?? await createParent(ctx, agentId, l.transaction_type, side.quarter, listingCity(l))
  const sourceId = crypto.randomUUID()

  const cols: Record<string, unknown> = {
    id: sourceId, property_id: propertyId, agency_id: agencyId, source_type: 'url', source_url: url,
    site_key: run.site_key, transaction_type: l.transaction_type, price_at_source: price,
    price_on_request: b01(l.price_on_request) ?? 0, currency: l.currency || 'EUR', listing_title: str(l.title),
    status: 'active', first_seen_at: ctx.now, last_seen_at: ctx.now, last_run_id: run.id,
    detail_scraped_at: ctx.now, last_checked_at: ctx.now, created_at: ctx.now,
    match_tier: decision.autoTo?.tier ?? 'new', ...d,
  }
  const keys = Object.keys(cols)
  await ctx.db.prepare(`INSERT INTO property_sources (${keys.join(',')}) VALUES (${keys.map(() => '?').join(',')})`)
    .bind(...keys.map(k => cols[k] ?? null)).run()
  if (price != null) {
    await ctx.db.prepare(
      `INSERT INTO price_history (id, source_id, property_id, price, previous_price, currency, observed_at) VALUES (?, ?, ?, ?, NULL, ?, ?)`
    ).bind(crypto.randomUUID(), sourceId, propertyId, price, l.currency || 'EUR', ctx.now).run()
  }
  for (const r of decision.review) await queueReview(ctx, sourceId, r.id, r.m)
  await refreshParent(ctx, propertyId, run.id)
  await ctx.event('new', sourceId, propertyId, run.site_key, run.id, {
    matched_existing: !!decision.autoTo, tier: decision.autoTo?.tier ?? null, review_candidates: decision.review.length,
  })
  run._new++
  return { source_url: url, outcome: 'new', source_id: sourceId, property_id: propertyId, match: decision.autoTo?.tier ?? (decision.review.length ? 'review' : 'none'), events: ['new'] }
}

// Detail-page columns shared by insert and update, with geo resolution:
// listing coords (inside Monaco) → gazetteer building → quarter centroid.
// Outside Monaco the gazetteer and quarters don't apply: listing coords only.
async function detailColumns(ctx: Ctx, l: ListingPayload): Promise<Record<string, unknown>> {
  const monaco = listingCity(l) === 'Monaco'
  const quarters = monaco ? await ctx.getQuarters() : []
  const building = monaco ? await ctx.findBuilding(l.building_name) : null
  const quarter = !monaco ? null : resolveQuarter(l.quarter, quarters) ?? resolveQuarter(l.address, quarters) ?? building?.quarter
    ?? resolveQuarter(l.title, quarters) ?? resolveQuarter((l.description || '').slice(0, 300), quarters) ?? null
  let lat: number | null = null, lng: number | null = null, coordSource: string | null = null
  if (monaco ? inMonaco(l.lat, l.lng) : inRiviera(l.lat, l.lng)) { lat = round5(l.lat!); lng = round5(l.lng!); coordSource = 'listing' }
  else if (building?.lat != null) { lat = building.lat; lng = building.lng; coordSource = 'building' }
  else if (quarter) {
    const q = quarters.find(x => x.id === quarter)
    if (q?.centroid_lat != null) { lat = q.centroid_lat; lng = q.centroid_lng; coordSource = 'quarter' }
  }
  return {
    listing_description: str(l.description), rent_period: l.transaction_type === 'rent' ? (l.rent_period || 'month') : null,
    property_type: str(l.property_type), bedrooms: num(l.bedrooms), rooms: num(l.rooms), bathrooms: num(l.bathrooms),
    living_area_sqm: num(l.living_area_sqm), terrace_sqm: num(l.terrace_sqm), floor: num(l.floor),
    parking: num(l.parking), cellar: b01(l.cellar), sea_view: b01(l.sea_view),
    building_name: str(l.building_name), building_id: building?.id ?? null, address: str(l.address),
    quarter, lat, lng, coord_source: coordSource, external_ref: str(l.external_ref),
    agent_name: str(l.agent_name), agent_phone: str(l.agent_phone), agent_email: str(l.agent_email),
    agent_whatsapp: str(l.agent_whatsapp), agency_phone: str(l.agency_phone), agency_email: str(l.agency_email),
    photo_urls: Array.isArray(l.photo_urls) ? JSON.stringify(l.photo_urls.filter(u => typeof u === 'string')) : null,
    photo_phashes: phashList(l.photo_phashes),
    extra: l.extra ? JSON.stringify(l.extra) : null,
  }
}

// ── run lifecycle ────────────────────────────────────────────────────────

const MAX_BATCH = 25
const MISSED_DAYS_TO_REMOVE = 3
const BLOCKED_DAYS_TO_FLAG_LOCAL = 3

interface AgencyPayload { name: string; website?: string; phone?: string; email?: string; cim_slug?: string; manager?: string; address?: string }

// One agency can be scraped from several sites (its CIM portal page and its
// own website): the first site to see it owns agencies.site_key, later ones
// find it by cim_slug. Runs carry agency_id so attribution never depends on it.
async function upsertAgency(ctx: Ctx, siteKey: string, a: AgencyPayload | undefined): Promise<string> {
  const row = await ctx.db.prepare('SELECT id FROM agencies WHERE site_key = ?').bind(siteKey).first<{ id: string }>()
    ?? (str(a?.cim_slug) ? await ctx.db.prepare('SELECT id FROM agencies WHERE cim_slug = ?').bind(str(a!.cim_slug)).first<{ id: string }>() : null)
  if (row) {
    if (a) {
      await ctx.db.prepare(
        `UPDATE agencies SET name = COALESCE(?, name), website = COALESCE(?, website), phone = COALESCE(?, phone),
           email = COALESCE(?, email), cim_slug = COALESCE(?, cim_slug), manager = COALESCE(?, manager),
           address = COALESCE(?, address), updated_at = ? WHERE id = ?`
      ).bind(str(a.name), str(a.website), str(a.phone), str(a.email), str(a.cim_slug), str(a.manager), str(a.address), ctx.now, row.id).run()
    }
    return row.id
  }
  const id = crypto.randomUUID()
  await ctx.db.prepare(
    `INSERT INTO agencies (id, name, site_key, website, phone, email, cim_slug, manager, address, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(id, str(a?.name) ?? siteKey, siteKey, str(a?.website), str(a?.phone), str(a?.email), str(a?.cim_slug),
    str(a?.manager), str(a?.address), ctx.now, ctx.now).run()
  return id
}

async function finishRun(ctx: Ctx, run: any, body: any): Promise<Json> {
  const status = ['ok', 'partial', 'blocked', 'failed'].includes(body.status) ? body.status : 'failed'
  const indexComplete = status === 'ok' && body.index_complete === true
  const indexCount = num(body.index_count)
  const health = await ctx.db.prepare('SELECT * FROM site_health WHERE site_key = ?').bind(run.site_key).first<any>()
  const baseline = health?.baseline_index_count ?? null

  // A crawl that suddenly sees 0 listings or under half its usual count is
  // a broken scraper, not a market event: no removals, flag for repair.
  const suspicious = indexComplete && (indexCount === 0 || (baseline != null && indexCount != null && indexCount < baseline * 0.5))
  let removed = 0
  if (indexComplete && !suspicious) {
    const { results } = await ctx.db.prepare(
      `SELECT id, property_id, missed_days, last_missed_date FROM property_sources
       WHERE site_key = ? AND removed_at IS NULL AND is_off_market = 0
         AND substr(COALESCE(last_seen_at, ''), 1, 10) < ?
         AND (last_missed_date IS NULL OR last_missed_date < ?)`
    ).bind(run.site_key, ctx.today, ctx.today).all<any>()
    const touched = new Set<string>()
    for (const s of results || []) {
      const days = (s.missed_days || 0) + 1
      if (days >= MISSED_DAYS_TO_REMOVE) {
        await ctx.db.prepare(
          `UPDATE property_sources SET missed_days = ?, last_missed_date = ?, removed_at = ?, is_off_market = 1 WHERE id = ?`
        ).bind(days, ctx.today, ctx.now, s.id).run()
        await ctx.event('removed', s.id, s.property_id, run.site_key, run.id, { missed_days: days })
        touched.add(s.property_id)
        removed++
      } else {
        await ctx.db.prepare('UPDATE property_sources SET missed_days = ?, last_missed_date = ? WHERE id = ?')
          .bind(days, ctx.today, s.id).run()
      }
    }
    for (const pid of touched) await refreshParent(ctx, pid, run.id)
  }

  await ctx.db.prepare(
    `UPDATE scrape_runs SET status = ?, index_complete = ?, index_count = ?, detail_count = ?, removed_count = ?,
       error = ?, finished_at = ? WHERE id = ?`
  ).bind(status, indexComplete ? 1 : 0, indexCount, num(body.detail_count), removed, str(body.error), ctx.now, run.id).run()

  const blocked = status === 'blocked'
  const ok = status === 'ok' || status === 'partial'
  const blocks = blocked ? (health?.consecutive_blocks ?? 0) + (health?.last_run_at?.slice(0, 10) === ctx.today && health?.last_status === 'blocked' ? 0 : 1) : 0
  const failures = status === 'failed' ? (health?.consecutive_failures ?? 0) + 1 : 0
  const newBaseline = indexComplete && !suspicious && indexCount != null
    ? (baseline == null ? indexCount : baseline * 0.8 + indexCount * 0.2) : baseline
  await ctx.db.prepare(
    `INSERT INTO site_health (site_key, agency_id, runner, last_status, last_run_at, last_success_at, consecutive_blocks,
       consecutive_failures, last_index_count, baseline_index_count, flag_broken, flag_move_local, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(site_key) DO UPDATE SET agency_id = excluded.agency_id, runner = excluded.runner,
       last_status = excluded.last_status, last_run_at = excluded.last_run_at,
       last_success_at = COALESCE(excluded.last_success_at, site_health.last_success_at),
       consecutive_blocks = excluded.consecutive_blocks, consecutive_failures = excluded.consecutive_failures,
       last_index_count = COALESCE(excluded.last_index_count, site_health.last_index_count),
       baseline_index_count = excluded.baseline_index_count, flag_broken = excluded.flag_broken,
       flag_move_local = MAX(site_health.flag_move_local, excluded.flag_move_local), updated_at = excluded.updated_at`
  ).bind(
    run.site_key, run.agency_id, run.runner, status, ctx.now, ok ? ctx.now : null, blocks, failures, indexCount,
    newBaseline, suspicious || failures >= 3 ? 1 : 0, blocks >= BLOCKED_DAYS_TO_FLAG_LOCAL && run.runner === 'server' ? 1 : 0, ctx.now,
  ).run()

  return { run_id: run.id, status, index_complete: indexComplete, removed, suspicious_count: suspicious, new: run.new_count, price_changes: run.price_change_count }
}

async function buildChangelog(ctx: Ctx, date: string): Promise<Json> {
  const { results: events } = await ctx.db.prepare(
    `SELECT e.type, e.site_key, e.data, e.created_at, e.property_id, s.source_url, s.listing_title, s.price_at_source,
            s.currency, s.transaction_type, s.bedrooms, s.living_area_sqm, s.quarter
     FROM listing_events e LEFT JOIN property_sources s ON s.id = e.source_id
     WHERE e.created_at >= ? AND e.created_at < ? ORDER BY e.created_at`
  ).bind(date, date + '￿').all<any>()
  const { results: runs } = await ctx.db.prepare(
    `SELECT site_key, status FROM scrape_runs WHERE started_at >= ? AND started_at < ? ORDER BY started_at`
  ).bind(date, date + '￿').all<any>()
  const heroes = await ctx.db.prepare(
    `SELECT COUNT(*) AS n FROM property_sources WHERE hero_saved_at >= ? AND hero_saved_at < ?`
  ).bind(date, date + '￿').first<{ n: number }>()
  const totals = await ctx.db.prepare(
    `SELECT COUNT(*) AS sources, COUNT(DISTINCT property_id) AS properties FROM property_sources
     WHERE site_key IS NOT NULL AND removed_at IS NULL AND is_off_market = 0`
  ).first<{ sources: number; properties: number }>()

  // Latest status per site for the day.
  const siteStatus = new Map<string, string>()
  for (const r of runs || []) siteStatus.set(r.site_key, r.status)
  const sites = { ok: 0, partial: 0, blocked: 0, failed: 0, running: 0 } as Record<string, number>
  for (const s of siteStatus.values()) sites[s] = (sites[s] ?? 0) + 1

  const byType: Record<string, unknown[]> = {}
  for (const e of events || []) {
    (byType[e.type] ||= []).push({
      site_key: e.site_key, property_id: e.property_id, url: e.source_url, title: e.listing_title,
      price: e.price_at_source, currency: e.currency, transaction_type: e.transaction_type,
      bedrooms: e.bedrooms, living_area_sqm: e.living_area_sqm, quarter: e.quarter,
      data: e.data ? JSON.parse(e.data) : null, at: e.created_at,
    })
  }
  const summary = {
    date,
    live_listings: totals?.sources ?? 0,
    live_properties: totals?.properties ?? 0,
    new: byType.new?.length ?? 0,
    removed: byType.removed?.length ?? 0,
    price_drops: byType.price_drop?.length ?? 0,
    price_rises: byType.price_rise?.length ?? 0,
    relisted: byType.relisted?.length ?? 0,
    merged: byType.merged?.length ?? 0,
    heroes_saved: heroes?.n ?? 0,
    sites,
    pending_reviews: (await ctx.db.prepare(`SELECT COUNT(*) AS n FROM review_queue WHERE status = 'pending'`).first<{ n: number }>())?.n ?? 0,
  }
  return { summary, events: byType }
}

// ── /sync/* (runner, Bearer SYNC_API_TOKEN — auth checked by caller) ────

export async function handlePipelineSync(req: Request, env: Env, path: string, respond: Respond): Promise<Response | null> {
  const ctx = new Ctx(env, new Date().toISOString())

  // POST /sync/runs {site_key, runner, mode, agency?}
  if (path === '/sync/runs' && req.method === 'POST') {
    const body = await req.json<any>()
    const siteKey = str(body.site_key)
    if (!siteKey || !/^[a-z0-9_-]+$/.test(siteKey)) return respond({ error: 'site_key must match [a-z0-9_-]+' }, 400)
    const runner = body.runner === 'local' ? 'local' : 'server'
    const mode = body.mode === 'light' ? 'light' : 'full'
    const agencyId = await upsertAgency(ctx, siteKey, body.agency)
    const id = crypto.randomUUID()
    await ctx.db.prepare(
      `INSERT INTO scrape_runs (id, site_key, agency_id, runner, mode, status, started_at) VALUES (?, ?, ?, ?, ?, 'running', ?)`
    ).bind(id, siteKey, agencyId, runner, mode, ctx.now).run()
    return respond({ run_id: id, agency_id: agencyId }, 201)
  }

  // GET /sync/sites/:key/known — what the Worker already has for this site,
  // so the runner only opens detail pages for new/changed/stale listings.
  const knownMatch = path.match(/^\/sync\/sites\/([a-z0-9_-]+)\/known$/)
  if (knownMatch && req.method === 'GET') {
    const { results } = await ctx.db.prepare(
      `SELECT id, source_url, price_at_source AS price, listing_title AS title, detail_scraped_at, last_seen_at,
              hero_image_key IS NOT NULL AS has_hero, photo_phashes IS NOT NULL AS has_phashes,
              removed_at IS NOT NULL AS removed
       FROM property_sources WHERE site_key = ?`
    ).bind(knownMatch[1]).all()
    return respond(results || [])
  }

  const runMatch = path.match(/^\/sync\/runs\/([^/]+)\/(listings|finish)$/)
  if (runMatch && req.method === 'POST') {
    const run = await ctx.db.prepare(
      `SELECT * FROM scrape_runs WHERE id = ?`
    ).bind(runMatch[1]).first<any>()
    if (!run) return respond({ error: 'Run not found' }, 404)
    if (run.status !== 'running') return respond({ error: `Run already ${run.status}` }, 409)

    if (runMatch[2] === 'finish') return respond(await finishRun(ctx, run, await req.json<any>()))

    const body = await req.json<{ listings: ListingPayload[] }>()
    const listings = Array.isArray(body.listings) ? body.listings : []
    if (listings.length > MAX_BATCH) return respond({ error: `At most ${MAX_BATCH} listings per request` }, 413)
    const agentId = await pipelineAgentId(env)
    run._new = 0; run._price_changes = 0
    const results: ListingResult[] = []
    // Each listing is isolated: one bad record never fails the batch.
    for (const l of listings) {
      try {
        results.push(await ingestListing(ctx, run, run.agency_id, agentId, l))
      } catch (e: any) {
        console.error('ingestListing failed', l?.source_url, e)
        results.push({ source_url: String(l?.source_url), outcome: 'error', error: String(e?.message || e) })
      }
    }
    const updated = results.filter(r => r.outcome === 'updated').length
    await ctx.db.prepare(
      `UPDATE scrape_runs SET new_count = new_count + ?, updated_count = updated_count + ?, price_change_count = price_change_count + ? WHERE id = ?`
    ).bind(run._new, updated, run._price_changes, run.id).run()
    return respond({ results })
  }

  // PUT /sync/sources/:id/hero — body: WebP bytes; header X-Phash: 16 hex chars.
  const heroMatch = path.match(/^\/sync\/sources\/([^/]+)\/hero$/)
  if (heroMatch && req.method === 'PUT') {
    const source = await ctx.db.prepare('SELECT * FROM property_sources WHERE id = ?').bind(heroMatch[1]).first<any>()
    if (!source) return respond({ error: 'Not found' }, 404)
    const bytes = await req.arrayBuffer()
    if (bytes.byteLength === 0 || bytes.byteLength > 5_000_000) return respond({ error: 'Hero must be 1 B – 5 MB' }, 400)
    const phash = (req.headers.get('X-Phash') || '').toLowerCase()
    if (await ctx.isGeneric(phash)) return respond({ error: 'generic image (logo / stock photo)', generic: true }, 422)
    const key = `heroes/${source.id}.webp`
    await env.DOCS.put(key, bytes, { httpMetadata: { contentType: 'image/webp' } })
    await ctx.db.prepare('UPDATE property_sources SET hero_image_key = ?, hero_phash = ?, hero_saved_at = ? WHERE id = ?')
      .bind(key, /^[0-9a-f]{16}$/.test(phash) ? phash : null, ctx.now, source.id).run()
    await ctx.db.prepare('UPDATE properties SET hero_image_key = COALESCE(hero_image_key, ?) WHERE id = ?').bind(key, source.property_id).run()
    const merge = /^[0-9a-f]{16}$/.test(phash) ? await phashMatch(ctx, { ...source, hero_phash: phash }) : null
    return respond({ key, phash_match: merge })
  }

  // POST /sync/changelog {date?} — build the day's changelog and store it in R2.
  if (path === '/sync/changelog' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const date = /^\d{4}-\d{2}-\d{2}$/.test(body?.date || '') ? body.date : ctx.today
    const log = await buildChangelog(ctx, date)
    const key = `changelogs/${date}.json`
    await env.DOCS.put(key, JSON.stringify(log), { httpMetadata: { contentType: 'application/json' } })
    return respond({ key, summary: log.summary })
  }

  // POST /sync/rematch {after?, limit?} — re-run photo matching for stored
  // heroes (after the rules change). Pending photo reviews it resolves are
  // closed as merged. Paged by source id to stay within request limits.
  if (path === '/sync/rematch' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 20, 40)
    const { results } = await ctx.db.prepare(
      `SELECT * FROM property_sources WHERE hero_phash IS NOT NULL AND removed_at IS NULL AND id > ? ORDER BY id LIMIT ?`
    ).bind(String(body?.after || ''), limit).all<any>()
    let merged = 0
    for (const src of results || []) {
      const current = await ctx.db.prepare('SELECT * FROM property_sources WHERE id = ?').bind(src.id).first<any>()
      const r = current ? await phashMatch(ctx, current) : null
      if (r?.action === 'merged') {
        merged++
        await ctx.db.prepare(
          `UPDATE review_queue SET status = 'merged', decided_at = ? WHERE source_id = ? AND candidate_property_id = ? AND status = 'pending'`
        ).bind(ctx.now, src.id, r.into).run()
      }
    }
    const rows = results || []
    const last = rows.length ? rows[rows.length - 1].id : null
    if (!body?.after) {
      await ctx.db.prepare(
        `UPDATE review_queue SET status = 'merged', decided_at = ? WHERE status = 'pending'
         AND candidate_property_id = (SELECT property_id FROM property_sources WHERE id = review_queue.source_id)`
      ).bind(ctx.now).run()
    }
    return respond({ processed: (results || []).length, merged, next: (results || []).length === limit ? last : null })
  }

  // POST /sync/rematch-ids {after?, limit?} — join agency-site listings to the
  // same agency's CIM listing by shared id when they were stored apart.
  if (path === '/sync/rematch-ids' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 30, 50)
    const { results } = await ctx.db.prepare(
      `SELECT id, property_id, agency_id, extra FROM property_sources
       WHERE extra LIKE '%"cim_id"%' AND removed_at IS NULL AND id > ? ORDER BY id LIMIT ?`
    ).bind(String(body?.after || ''), limit).all<any>()
    let merged = 0
    for (const src of results || []) {
      const cimId = (() => { try { return JSON.parse(src.extra).cim_id } catch { return null } })()
      if (!cimId || !src.agency_id) continue
      const cim = await ctx.db.prepare(
        `SELECT property_id FROM property_sources WHERE source_url = ? AND agency_id = ? AND removed_at IS NULL`
      ).bind(`https://www.chambre-immobiliere-monaco.mc/fr/bien/${cimId}/bien`, src.agency_id).first<{ property_id: string }>()
      const cur = await ctx.db.prepare('SELECT property_id FROM property_sources WHERE id = ?').bind(src.id).first<{ property_id: string }>()
      if (cim && cur && cim.property_id !== cur.property_id) {
        const { results: all } = await ctx.db.prepare('SELECT id FROM property_sources WHERE property_id = ?').bind(cur.property_id).all<{ id: string }>()
        for (const r of all || []) await moveSource(ctx, r.id, cur.property_id, cim.property_id, 'ref', null)
        merged++
      }
    }
    const rows = results || []
    return respond({ processed: rows.length, merged, next: rows.length === limit ? rows[rows.length - 1].id : null })
  }

  // POST /sync/gazetteer — (re)build buildings from listings that carry
  // exact coordinates (CIM): one row per normalised building name, median
  // position, most common quarter. Names seen with coordinates >300 m apart
  // are ambiguous (two "Le Palais" etc.) and skipped.
  if (path === '/sync/gazetteer' && req.method === 'POST') {
    const { results } = await ctx.db.prepare(
      `SELECT building_name, lat, lng, quarter FROM property_sources
       WHERE coord_source = 'listing' AND building_name IS NOT NULL AND removed_at IS NULL
         AND lat BETWEEN 43.72 AND 43.76 AND lng BETWEEN 7.40 AND 7.45`
    ).all<any>()
    const groups = new Map<string, { names: Map<string, number>; pts: [number, number][]; q: Map<string, number> }>()
    for (const r of results || []) {
      const norm = normalizeBuildingName(r.building_name)
      if (!norm || norm.length < 3) continue
      const g = groups.get(norm) ?? { names: new Map<string, number>(), pts: [] as [number, number][], q: new Map<string, number>() }
      g.names.set(r.building_name, (g.names.get(r.building_name) ?? 0) + 1)
      g.pts.push([r.lat, r.lng])
      if (r.quarter) g.q.set(r.quarter, (g.q.get(r.quarter) ?? 0) + 1)
      groups.set(norm, g)
    }
    const median = (xs: number[]) => { const a = [...xs].sort((x, y) => x - y); return a[Math.floor(a.length / 2)] }
    const top = (m: Map<string, number>) => [...m.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null
    let upserted = 0, ambiguous = 0
    const stmts: D1PreparedStatement[] = []
    for (const [norm, g] of groups) {
      const lat = median(g.pts.map(p => p[0])), lng = median(g.pts.map(p => p[1]))
      const spread = Math.max(...g.pts.map(p => Math.hypot((p[0] - lat) * 111000, (p[1] - lng) * 80000)))
      if (spread > 300) { ambiguous++; continue }
      stmts.push(ctx.db.prepare(
        `INSERT INTO buildings (id, name, normalized_name, aliases, quarter, lat, lng, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id) DO NOTHING`
      ).bind(`b-${norm.replace(/\s+/g, '-')}`, top(g.names), norm, JSON.stringify([...g.names.keys()]), top(g.q), round5(lat), round5(lng), ctx.now))
      stmts.push(ctx.db.prepare(
        `UPDATE buildings SET name = ?, aliases = ?, quarter = COALESCE(?, quarter), lat = ?, lng = ? WHERE id = ?`
      ).bind(top(g.names), JSON.stringify([...g.names.keys()]), top(g.q), round5(lat), round5(lng), `b-${norm.replace(/\s+/g, '-')}`))
      upserted++
    }
    for (let i = 0; i < stmts.length; i += 50) await ctx.db.batch(stmts.slice(i, i + 50))
    return respond({ buildings: upserted, ambiguous })
  }

  // POST /sync/regeo {after?, limit?} — give listings without their own
  // coordinates the building position (and building_id) from the gazetteer.
  if (path === '/sync/regeo' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 200, 500)
    const { results } = await ctx.db.prepare(
      `SELECT id, property_id, building_name FROM property_sources
       WHERE (coord_source IS NULL OR coord_source = 'quarter') AND building_name IS NOT NULL AND id > ?
         AND (extra IS NULL OR extra NOT LIKE '%"city":%' OR extra LIKE '%"city":"Monaco"%')
       ORDER BY id LIMIT ?`
    ).bind(String(body?.after || ''), limit).all<any>()
    const touched = new Set<string>()
    for (const r of results || []) {
      const b = await ctx.findBuilding(r.building_name)
      if (b?.lat == null) continue
      await ctx.db.prepare(
        `UPDATE property_sources SET lat = ?, lng = ?, coord_source = 'building', building_id = ?, quarter = COALESCE(quarter, ?) WHERE id = ?`
      ).bind(b.lat, b.lng, b.id, b.quarter, r.id).run()
      touched.add(r.property_id)
    }
    for (const pid of touched) await refreshParent(ctx, pid, null)
    const rows = results || []
    return respond({ processed: rows.length, located: touched.size, next: rows.length === limit ? rows[rows.length - 1].id : null })
  }

  // POST /sync/fix-data {after?, limit?} — apply saneTransaction to stored
  // listings and give properties from the first export (no extra.city then)
  // the town their listings name. Paged by property id.
  if (path === '/sync/fix-data' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 200, 400)
    const { results } = await ctx.db.prepare(
      `SELECT id, city, transaction_type FROM properties WHERE origin = 'pipeline' AND merged_into IS NULL AND id > ? ORDER BY id LIMIT ?`
    ).bind(String(body?.after || ''), limit).all<any>()
    let fixedSources = 0, fixedTx = 0, fixedCity = 0
    for (const p of results || []) {
      const { results: srcs } = await ctx.db.prepare(
        `SELECT id, transaction_type, price_at_source, price_on_request, extra, listing_title, listing_description
         FROM property_sources WHERE property_id = ? AND removed_at IS NULL`
      ).bind(p.id).all<any>()
      if (!srcs?.length) continue
      const txs: string[] = []
      for (const s of srcs) {
        const sane = saneTransaction(s.transaction_type, s.price_on_request ? null : s.price_at_source)
        if (sane.tx !== s.transaction_type || (sane.price == null && s.price_at_source != null && !s.price_on_request)) {
          await ctx.db.prepare(
            `UPDATE property_sources SET transaction_type = ?, price_at_source = ?, price_on_request = ?, rent_period = ? WHERE id = ?`
          ).bind(sane.tx, sane.price, sane.price == null ? 1 : 0, sane.tx === 'rent' ? 'month' : null, s.id).run()
          fixedSources++
        }
        txs.push(sane.tx as string)
      }
      const tx = txs.sort((a, b) => txs.filter(x => x === b).length - txs.filter(x => x === a).length)[0]
      const stated = (s: any): string | null => {
        try { const c = JSON.parse(s.extra || '{}').city; if (typeof c === 'string' && c) return c } catch { /* no extra */ }
        return townInText(s.listing_title, null)  // titles only: descriptions mention neighbouring towns
      }
      const cities = srcs.map(stated)
      // Every listing must name the same town, outside Monaco, to move the property.
      const city = cities.every(c => c && c === cities[0]) && cities[0] !== 'Monaco' ? cities[0] as string : p.city
      if (tx !== p.transaction_type || city !== p.city) {
        await ctx.db.prepare(
          `UPDATE properties SET transaction_type = ?, city = ?, address = CASE WHEN ? != city THEN ? ELSE address END,
             zip_code = CASE WHEN ? = 'Monaco' THEN zip_code ELSE '' END, quarter = CASE WHEN ? = 'Monaco' THEN quarter ELSE NULL END
           WHERE id = ?`).bind(tx, city, city, city, city, city, p.id).run()
        if (tx !== p.transaction_type) fixedTx++
        if (city !== p.city) fixedCity++
      }
      if (fixedSources) await refreshParent(ctx, p.id, null)
    }
    const rows = results || []
    return respond({ processed: rows.length, fixedSources, fixedTx, fixedCity, next: rows.length === limit ? rows[rows.length - 1].id : null })
  }

  // POST /sync/generic-photos — rebuild the set of generic images (logos,
  // portraits, stock views): seen on ≥3 properties whose sizes differ by >15%,
  // or on ≥5 with no sizes known. One flat listed by many agencies has one size.
  if (path === '/sync/generic-photos' && req.method === 'POST') {
    const stats = `COUNT(DISTINCT s.property_id) AS n, COUNT(s.living_area_sqm) AS known,
                   MIN(s.living_area_sqm) AS amin, MAX(s.living_area_sqm) AS amax`
    const { results: heroes } = await ctx.db.prepare(
      `SELECT s.hero_phash AS h, ${stats} FROM property_sources s
       WHERE s.hero_phash IS NOT NULL AND s.removed_at IS NULL GROUP BY s.hero_phash HAVING n >= 3`).all<any>()
    const { results: gallery } = await ctx.db.prepare(
      `SELECT j.value AS h, ${stats} FROM property_sources s, json_each(s.photo_phashes) j
       WHERE s.photo_phashes IS NOT NULL AND s.removed_at IS NULL GROUP BY j.value HAVING n >= 3`).all<any>()
    const generic = new Set<string>(['0000000000000000', 'ffffffffffffffff'])
    for (const r of [...(heroes || []), ...(gallery || [])]) {
      // One flat advertised by many agencies keeps its size; a logo or a stock
      // view sits on flats of every size (or on listings without one).
      const spread = r.amin > 0 && r.amax / r.amin > 1.15
      if (spread || (r.n >= 5 && r.known < 2)) generic.add(r.h)
    }
    await env.DOCS.put(GENERIC_KEY, JSON.stringify([...generic]), { httpMetadata: { contentType: 'application/json' } })
    return respond({ generic: generic.size })
  }

  // POST /sync/clean-generic-heroes {after?, limit?} — drop logo/stock covers;
  // the listing's detail is marked stale so the next run uploads a real photo.
  if (path === '/sync/clean-generic-heroes' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 300, 500)
    const { results } = await ctx.db.prepare(
      `SELECT id, property_id, hero_image_key, hero_phash FROM property_sources
       WHERE hero_phash IS NOT NULL AND id > ? ORDER BY id LIMIT ?`).bind(String(body?.after || ''), limit).all<any>()
    let cleared = 0
    for (const r of results || []) {
      if (!(await ctx.isGeneric(r.hero_phash))) continue
      await ctx.db.prepare(
        `UPDATE property_sources SET hero_image_key = NULL, hero_phash = NULL, hero_saved_at = NULL,
           detail_scraped_at = '2000-01-01T00:00:00Z' WHERE id = ?`).bind(r.id).run()
      const other = await ctx.db.prepare(
        `SELECT hero_image_key FROM property_sources WHERE property_id = ? AND hero_image_key IS NOT NULL AND removed_at IS NULL LIMIT 1`
      ).bind(r.property_id).first<{ hero_image_key: string }>()
      await ctx.db.prepare(`UPDATE properties SET hero_image_key = ? WHERE id = ? AND hero_image_key = ?`)
        .bind(other?.hero_image_key ?? null, r.property_id, r.hero_image_key).run()
      cleared++
    }
    const rows = results || []
    return respond({ processed: rows.length, cleared, next: rows.length === limit ? rows[rows.length - 1].id : null })
  }

  // POST /sync/detach-generic {limit?} — listings that joined their property
  // only through a generic image (logo, stock view) get their own parent and
  // are re-matched under the current rules; every other merge stays.
  if (path === '/sync/detach-generic' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 15, 30)
    const { results } = await ctx.db.prepare(
      `SELECT s.* FROM property_sources s JOIN properties p ON p.id = s.property_id
       WHERE s.match_tier = 'phash' AND s.removed_at IS NULL AND p.merged_into IS NULL AND s.hero_phash IS NOT NULL
         AND p.source_count > 1 AND s.id > ? ORDER BY s.id LIMIT 400`).bind(String(body?.after || '')).all<any>()
    const agentId = await pipelineAgentId(env)
    let detached = 0, rejoined = 0, reviews = 0, last: string | null = null
    for (const cur of results || []) {
      last = cur.id
      if (!(await ctx.isGeneric(cur.hero_phash))) continue
      const np = await createParent(ctx, agentId, cur.transaction_type, cur.quarter, sourceCity(cur.extra))
      await moveSource(ctx, cur.id, cur.property_id, np, 'new', null)
      await refreshParent(ctx, np, null)
      detached++
      const side: MatchSide = {
        lat: cur.lat, lng: cur.lng, coord_source: cur.coord_source, area: cur.living_area_sqm, bedrooms: cur.bedrooms,
        floor: cur.floor, building_id: cur.building_id, building_norm: normalizeBuildingName(cur.building_name),
        quarter: cur.quarter, price: cur.price_on_request ? null : cur.price_at_source,
      }
      const d = await findMatches(ctx, side, cur.transaction_type, cur.site_key, false, np, sourceCity(cur.extra))
      if (!d.autoTo && d.review.length) await galleryDecide(ctx, d, cur.agency_id, cur.photo_phashes, side)
      if (d.autoTo && compatible(await profileOf(ctx, np), await profileOf(ctx, d.autoTo.id))) {
        await moveSource(ctx, cur.id, np, d.autoTo.id, d.autoTo.tier, null)
        await refreshParent(ctx, d.autoTo.id, null)
        rejoined++
      } else {
        for (const r of d.review) { await queueReview(ctx, cur.id, r.id, r.m); reviews++ }
      }
      if (detached >= limit) break
    }
    const done = !results?.length || (results.length < 400 && last === results[results.length - 1].id && detached < limit)
    return respond({ detached, rejoined, reviews, next: done ? null : last })
  }

  // POST /sync/generic-merges — properties holding a listing that joined by a
  // photo match on what is now known to be a generic image (to /sync/split).
  if (path === '/sync/generic-merges' && req.method === 'POST') {
    const { results } = await ctx.db.prepare(
      `SELECT s.property_id, s.hero_phash, s.photo_phashes FROM property_sources s JOIN properties p ON p.id = s.property_id
       WHERE s.match_tier IN ('phash', 'photos') AND s.removed_at IS NULL AND p.merged_into IS NULL`).all<any>()
    const ids = new Set<string>()
    for (const r of results || []) {
      if (await ctx.isGeneric(r.hero_phash)) ids.add(r.property_id)
    }
    return respond({ property_ids: [...ids] })
  }

  // POST /sync/rematch-town {after?, limit?} — re-run matching for listings
  // outside Monaco (town-level rule + gallery photos), for those ingested
  // before it existed. Paged by source id.
  if (path === '/sync/rematch-town' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 100, 200)
    const { results } = await ctx.db.prepare(
      `SELECT s.*, p.city AS p_city FROM property_sources s JOIN properties p ON p.id = s.property_id
       WHERE s.removed_at IS NULL AND p.merged_into IS NULL AND p.origin = 'pipeline'
         AND (CASE WHEN ? = 'Monaco' THEN COALESCE(p.city, 'Monaco') = 'Monaco' ELSE COALESCE(p.city, 'Monaco') != 'Monaco' END)
         AND s.id > ? ORDER BY s.id LIMIT ?`
    ).bind(body?.city === 'Monaco' ? 'Monaco' : '', String(body?.after || ''), limit).all<any>()
    let merged = 0, reviews = 0
    for (const src of results || []) {
      const cur = await ctx.db.prepare('SELECT * FROM property_sources WHERE id = ?').bind(src.id).first<any>()
      if (!cur || cur.removed_at) continue
      const side: MatchSide = {
        lat: cur.lat, lng: cur.lng, coord_source: cur.coord_source, area: cur.living_area_sqm, bedrooms: cur.bedrooms,
        floor: cur.floor, building_id: cur.building_id, building_norm: normalizeBuildingName(cur.building_name),
        quarter: cur.quarter, price: cur.price_on_request ? null : cur.price_at_source,
      }
      const d = await findMatches(ctx, side, cur.transaction_type, cur.site_key, false, cur.property_id, src.p_city)
      if (!d.autoTo && d.review.length) await galleryDecide(ctx, d, cur.agency_id, cur.photo_phashes, side)
      if (d.autoTo && compatible(await profileOf(ctx, cur.property_id), await profileOf(ctx, d.autoTo.id))) {
        await moveSource(ctx, cur.id, cur.property_id, d.autoTo.id, d.autoTo.tier, null)
        await refreshParent(ctx, d.autoTo.id, null)
        merged++
      } else {
        for (const r of d.review) { await queueReview(ctx, cur.id, r.id, r.m); reviews++ }
      }
    }
    const rows = results || []
    return respond({ processed: rows.length, merged, reviews, next: rows.length === limit ? rows[rows.length - 1].id : null })
  }

  // POST /sync/split {property_id} — take an over-merged group apart and
  // re-match each listing under the current rules. The most complete listing
  // keeps the original parent (contact marks stay attached).
  if (path === '/sync/split' && req.method === 'POST') {
    const { property_id: pid } = await req.json<{ property_id: string }>()
    const { results } = await ctx.db.prepare(
      `SELECT * FROM property_sources WHERE property_id = ? AND removed_at IS NULL
       ORDER BY (living_area_sqm IS NOT NULL) + (floor IS NOT NULL) + (price_at_source IS NOT NULL) + (bedrooms IS NOT NULL) DESC, created_at`
    ).bind(pid).all<any>()
    const srcs = results || []
    if (srcs.length < 2) return respond({ property_id: pid, detached: 0 })
    const agentId = await pipelineAgentId(env)
    const detached: any[] = []
    for (const src of srcs.slice(1)) {
      const np = await createParent(ctx, agentId, src.transaction_type, src.quarter, sourceCity(src.extra))
      await ctx.db.prepare(`UPDATE property_sources SET property_id = ?, match_tier = 'new' WHERE id = ?`).bind(np, src.id).run()
      await ctx.db.prepare('UPDATE price_history SET property_id = ? WHERE source_id = ?').bind(np, src.id).run()
      detached.push({ ...src, property_id: np })
    }
    await refreshParent(ctx, pid, null)
    let rejoined = 0, reviews = 0
    for (const src of detached) {
      await refreshParent(ctx, src.property_id, null)
      const cur = await ctx.db.prepare('SELECT * FROM property_sources WHERE id = ?').bind(src.id).first<any>()
      if (!cur) continue
      const cimId = (() => { try { return JSON.parse(cur.extra || '{}').cim_id } catch { return null } })()
      const cimUrlId = String(cur.source_url).match(/chambre-immobiliere-monaco\.mc\/fr\/bien\/(\d+)\//)?.[1]
      const sameFlat = await ctx.db.prepare(
        `SELECT property_id FROM property_sources
         WHERE agency_id = ? AND property_id != ? AND removed_at IS NULL AND (
           (external_ref IS NOT NULL AND external_ref = ? AND site_key != ? AND transaction_type = ?)
           OR source_url = ? OR extra LIKE ?)
         LIMIT 1`
      ).bind(cur.agency_id, cur.property_id, cur.external_ref, cur.site_key, cur.transaction_type,
        cimId ? `https://www.chambre-immobiliere-monaco.mc/fr/bien/${cimId}/bien` : '-', cimUrlId ? `%"cim_id":"${cimUrlId}"%` : '-'
      ).first<{ property_id: string }>()
      let target: { id: string; tier: string } | null = sameFlat ? { id: sameFlat.property_id, tier: 'ref' } : null
      if (!target) {
        const side: MatchSide = {
          lat: cur.lat, lng: cur.lng, coord_source: cur.coord_source, area: cur.living_area_sqm, bedrooms: cur.bedrooms,
          floor: cur.floor, building_id: cur.building_id, building_norm: normalizeBuildingName(cur.building_name),
          quarter: cur.quarter, price: cur.price_on_request ? null : cur.price_at_source,
        }
        const d = await findMatches(ctx, side, cur.transaction_type, cur.site_key, false, cur.property_id, sourceCity(cur.extra))
        if (d.autoTo) target = d.autoTo
        for (const r of d.review) { await queueReview(ctx, cur.id, r.id, r.m); reviews++ }
      }
      if (target && compatible(await profileOf(ctx, cur.property_id), await profileOf(ctx, target.id))) {
        await moveSource(ctx, cur.id, cur.property_id, target.id, target.tier, null)
        rejoined++
      }
    }
    return respond({ property_id: pid, detached: detached.length, rejoined, reviews })
  }

  // POST /sync/refresh-parents {after?, limit?} — re-derive every pipeline
  // parent from its sources (after a change to the derivation rules).
  if (path === '/sync/refresh-parents' && req.method === 'POST') {
    const body = await req.json<any>().catch(() => ({}))
    const limit = Math.min(Number(body?.limit) || 100, 200)
    const { results } = await ctx.db.prepare(
      `SELECT id FROM properties WHERE origin = 'pipeline' AND merged_into IS NULL AND id > ? ORDER BY id LIMIT ?`
    ).bind(String(body?.after || ''), limit).all<{ id: string }>()
    const rows = results || []
    for (const r of rows) await refreshParent(ctx, r.id, null)
    return respond({ processed: rows.length, next: rows.length === limit ? rows[rows.length - 1].id : null })
  }

  // POST /sync/buildings {buildings:[{name, aliases?, quarter?, address?, lat?, lng?}]} — gazetteer upsert.
  // POST /sync/sites/:key/retire {reason} — a site found to be the wrong
  // website or to carry no Monaco listings: its live listings are removed
  // now (it will never run again to remove them the normal way).
  const retireMatch = path.match(/^\/sync\/sites\/([a-z0-9_-]+)\/retire$/)
  if (retireMatch && req.method === 'POST') {
    const { reason } = await req.json<{ reason?: string }>()
    const { results } = await ctx.db.prepare(
      'SELECT id, property_id FROM property_sources WHERE site_key = ? AND removed_at IS NULL'
    ).bind(retireMatch[1]).all<{ id: string; property_id: string }>()
    const touched = new Set<string>()
    for (const s of results || []) {
      await ctx.db.prepare('UPDATE property_sources SET removed_at = ?, is_off_market = 1 WHERE id = ?').bind(ctx.now, s.id).run()
      await ctx.db.prepare(`UPDATE review_queue SET status = 'rejected', decided_at = ? WHERE source_id = ? AND status = 'pending'`).bind(ctx.now, s.id).run()
      await ctx.event('removed', s.id, s.property_id, retireMatch[1], null, { retired: str(reason) ?? 'site retired' })
      touched.add(s.property_id)
    }
    for (const pid of touched) await refreshParent(ctx, pid, null)
    return respond({ site_key: retireMatch[1], removed: results?.length ?? 0 })
  }

  // POST /sync/phashes {items: [{source_id, phashes: [hex, ...]}]} — gallery
  // fingerprints computed outside a listing push (sync/photo_review.py).
  if (path === '/sync/phashes' && req.method === 'POST') {
    const { items } = await req.json<{ items: { source_id: string; phashes: string[] }[] }>()
    if (!Array.isArray(items) || items.length > 100) return respond({ error: 'items: array of at most 100' }, 400)
    let n = 0
    for (const it of items) {
      const v = phashList(it.phashes)
      if (!v || typeof it.source_id !== 'string') continue
      await ctx.db.prepare('UPDATE property_sources SET photo_phashes = ? WHERE id = ?').bind(v, it.source_id).run()
      n++
    }
    return respond({ updated: n })
  }

  // GET /sync/reviews?offset=0 — pending reviews with both sides' photo URLs,
  // for the local photo comparer (sync/photo_review.py). 100 per page.
  if (path === '/sync/reviews' && req.method === 'GET') {
    const offset = Math.max(0, Number(new URL(req.url).searchParams.get('offset')) || 0)
    const { results: items } = await ctx.db.prepare(
      `SELECT r.id, r.source_id, r.candidate_property_id, r.reasons, s.property_id AS source_property_id,
              s.agency_id, s.site_key, s.photo_urls
       FROM review_queue r JOIN property_sources s ON s.id = r.source_id
       WHERE r.status = 'pending' ORDER BY r.created_at, r.id LIMIT 100 OFFSET ?`
    ).bind(offset).all<any>()
    const ids = [...new Set((items || []).map(r => r.candidate_property_id))]
    const cands: any[] = ids.length ? (await ctx.db.prepare(
      `SELECT id, property_id, agency_id, site_key, photo_urls FROM property_sources
       WHERE removed_at IS NULL AND property_id IN (${ids.map(() => '?').join(',')})`
    ).bind(...ids).all<any>()).results || [] : []
    const parse = (p: string | null) => { try { return JSON.parse(p || '[]') } catch { return [] } }
    return respond((items || []).map(r => ({
      ...r, reasons: JSON.parse(r.reasons), photo_urls: parse(r.photo_urls),
      candidate_sources: cands.filter(c => c.property_id === r.candidate_property_id)
        .map(c => ({ id: c.id, agency_id: c.agency_id, site_key: c.site_key, photo_urls: parse(c.photo_urls) })),
    })))
  }

  // POST /sync/reviews/decide {decisions: [{id, decision: 'merge'|'reject', evidence}]}
  // Automatic decisions; a merge still has to pass the floor/price profile check.
  if (path === '/sync/reviews/decide' && req.method === 'POST') {
    const { decisions } = await req.json<{ decisions: { id: string; decision: string; evidence?: string }[] }>()
    if (!Array.isArray(decisions) || decisions.length > 25) return respond({ error: 'decisions: array of at most 25' }, 400)
    const out: Json[] = []
    for (const d of decisions) {
      if (d.decision !== 'merge' && d.decision !== 'reject') { out.push({ id: d.id, error: 'bad decision' }); continue }
      const item = await ctx.db.prepare(`SELECT * FROM review_queue WHERE id = ? AND status = 'pending'`).bind(d.id).first<any>()
      if (!item) { out.push({ id: d.id, skipped: 'not pending' }); continue }
      if (d.decision === 'merge') {
        const src = await ctx.db.prepare('SELECT floor, price_at_source AS p FROM property_sources WHERE id = ?').bind(item.source_id).first<any>()
        const mine: Profile = { floors: new Set(src?.floor != null ? [src.floor] : []), minP: src?.p || null, maxP: src?.p || null }
        if (!compatible(mine, await profileOf(ctx, item.candidate_property_id))) { out.push({ id: d.id, skipped: 'profile conflict' }); continue }
      }
      out.push(await decideReview(ctx, item, d.decision, null, str(d.evidence)))
    }
    return respond({ results: out })
  }

  if (path === '/sync/buildings' && req.method === 'POST') {
    const body = await req.json<{ buildings: any[] }>()
    const quarters = await ctx.getQuarters()
    let upserted = 0
    for (const b of body.buildings || []) {
      const norm = normalizeBuildingName(b.name)
      if (!norm) continue
      const quarter = resolveQuarter(b.quarter, quarters) ?? resolveQuarter(b.address, quarters)
      const lat = inMonaco(b.lat, b.lng) ? round5(b.lat) : null
      const lng = lat != null ? round5(b.lng) : null
      const aliases = JSON.stringify(Array.isArray(b.aliases) ? b.aliases : [])
      const existing = await ctx.db.prepare('SELECT id FROM buildings WHERE normalized_name = ?').bind(norm).first<{ id: string }>()
      if (existing) {
        await ctx.db.prepare(
          `UPDATE buildings SET name = ?, aliases = ?, quarter = COALESCE(?, quarter), address = COALESCE(?, address),
             lat = COALESCE(?, lat), lng = COALESCE(?, lng) WHERE id = ?`
        ).bind(b.name, aliases, quarter, str(b.address), lat, lng, existing.id).run()
      } else {
        await ctx.db.prepare(
          `INSERT INTO buildings (id, name, normalized_name, aliases, quarter, address, lat, lng, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
        ).bind(crypto.randomUUID(), b.name, norm, aliases, quarter, str(b.address), lat, lng, ctx.now).run()
      }
      upserted++
    }
    return respond({ upserted })
  }

  return null
}

// Tier 3: near-identical hero photo. Stock shots (a development's render
// used for every unit) are common, so the photo alone never auto-merges:
// it also needs area ±5% and equal bedrooms, a different agency, and the
// source must be alone under its parent. Anything weaker → review.
async function phashMatch(ctx: Ctx, source: any): Promise<Json | null> {
  if (await ctx.isGeneric(source.hero_phash)) return null
  const { results } = await ctx.db.prepare(
    `SELECT s.id, s.property_id, s.site_key, s.hero_phash, s.living_area_sqm, s.bedrooms, s.price_at_source, s.floor
     FROM property_sources s JOIN properties p ON p.id = s.property_id
     WHERE s.hero_phash IS NOT NULL AND s.id != ? AND s.property_id != ? AND s.transaction_type = ?
       AND s.removed_at IS NULL AND p.merged_into IS NULL`
  ).bind(source.id, source.property_id, source.transaction_type).all<any>()
  let best: any = null, bestD = 65
  for (const r of results || []) {
    // A building exterior shared by several units of different value is
    // not the same flat: prices more than 20% apart rule the pair out.
    if (source.price_at_source && r.price_at_source && !within(source.price_at_source, r.price_at_source, 0.2)) continue
    if (source.living_area_sqm && r.living_area_sqm && !within(source.living_area_sqm, r.living_area_sqm, 0.05)) continue
    if (source.bedrooms != null && r.bedrooms != null && source.bedrooms !== r.bedrooms) continue
    if (source.floor != null && r.floor != null && source.floor !== r.floor) continue
    const d = hamming64(source.hero_phash, r.hero_phash)
    if (d < bestD) { best = r; bestD = d }
  }
  if (!best || bestD > 6) return null

  const areaOk = within(source.living_area_sqm, best.living_area_sqm, 0.05)
  const bedsKnown = source.bedrooms != null && best.bedrooms != null
  const bedsOk = bedsKnown && source.bedrooms === best.bedrooms
  const priceOk = !(source.price_at_source && best.price_at_source) || within(source.price_at_source, best.price_at_source, 0.10)
  // Agencies of the property this source already sits in (it may be merged
  // with its own CIM/MCRE twin): the photo match must come from elsewhere.
  const { results: mine } = await ctx.db.prepare(
    'SELECT DISTINCT agency_id FROM property_sources WHERE property_id = ?'
  ).bind(source.property_id).all<{ agency_id: string }>()
  const bestAgency = await ctx.db.prepare('SELECT agency_id FROM property_sources WHERE id = ?').bind(best.id).first<{ agency_id: string }>()
  const otherAgency = !(mine || []).some(r => r.agency_id === bestAgency?.agency_id)
  const rejected = await ctx.db.prepare(
    `SELECT 1 FROM review_queue WHERE source_id = ? AND candidate_property_id = ? AND status = 'rejected'`
  ).bind(source.id, best.property_id).first()
  if (rejected) return null

  const reasons = ['hero_phash', ...(areaOk ? ['area_5pct'] : []), ...(bedsOk ? ['bedrooms'] : [])]
  const strictSame = bestD <= 2 && within(source.living_area_sqm, best.living_area_sqm, 0.03) && (bedsOk || !bedsKnown)
    && source.price_at_source && best.price_at_source && within(source.price_at_source, best.price_at_source, 0.05)
  const groupsOk = compatible(await profileOf(ctx, source.property_id), await profileOf(ctx, best.property_id))
  if (bestD <= 4 && areaOk && (bedsOk || !bedsKnown) && priceOk && (otherAgency || strictSame) && groupsOk) {
    const { results: all } = await ctx.db.prepare('SELECT id FROM property_sources WHERE property_id = ?').bind(source.property_id).all<{ id: string }>()
    const from = source.property_id
    for (const r of all || []) await moveSource(ctx, r.id, from, best.property_id, 'phash', null)
    return { action: 'merged', into: best.property_id, distance: bestD, moved: (all || []).length }
  }
  const rm: MatchResult = { tier: 'review', score: 0.5 + (areaOk ? 0.15 : 0) + (bedsOk ? 0.15 : 0), reasons }
  if (worthReview(rm, source.price_at_source, best.price_at_source, source.living_area_sqm, best.living_area_sqm)) {
    await queueReview(ctx, source.id, best.property_id, rm)
  }
  return { action: 'review', candidate: best.property_id, distance: bestD }
}

// ── /pipeline/* (CRM, logged-in agent — auth checked by caller) ──────────

export async function handlePipelineUi(req: Request, env: Env, url: URL, path: string, agentId: string | null, respond: Respond): Promise<Response> {
  const crm = await handleCrm(req, env, url, path, agentId, respond)
  if (crm) return crm
  const ctx = new Ctx(env, new Date().toISOString())

  // GET /pipeline/review?status=pending — both sides for a side-by-side view.
  if (path === '/pipeline/review' && req.method === 'GET') {
    const status = url.searchParams.get('status') || 'pending'
    const live = `r.status = ? AND s.removed_at IS NULL AND p.merged_into IS NULL AND s.property_id != r.candidate_property_id`
    const total = await ctx.db.prepare(
      `SELECT COUNT(*) AS n FROM review_queue r JOIN property_sources s ON s.id = r.source_id
       JOIN properties p ON p.id = r.candidate_property_id WHERE ${live}`).bind(status).first<{ n: number }>()
    const { results } = await ctx.db.prepare(
      `SELECT r.*, s.source_url, s.listing_title, s.price_at_source, s.price_on_request, s.currency, s.bedrooms, s.living_area_sqm,
              s.floor, s.building_name, s.quarter, s.hero_image_key, s.site_key, s.transaction_type, s.property_id AS source_property_id,
              json_extract(s.photo_urls, '$[0]') AS photo_url, (SELECT a.name FROM agencies a WHERE a.id = s.agency_id) AS agency_name,
              p.property_name AS candidate_title, p.price AS candidate_price, p.bedrooms AS candidate_bedrooms,
              p.living_area_sqm AS candidate_area, p.floor AS candidate_floor, p.building_name AS candidate_building,
              p.quarter AS candidate_quarter, p.city AS candidate_city, p.hero_image_key AS candidate_hero,
              p.source_count AS candidate_sources,
              (SELECT json_extract(c.photo_urls, '$[0]') FROM property_sources c WHERE c.property_id = p.id AND c.removed_at IS NULL
                 AND c.photo_urls LIKE '["http%' LIMIT 1) AS candidate_photo_url,
              (SELECT GROUP_CONCAT(DISTINCT a.name) FROM property_sources c JOIN agencies a ON a.id = c.agency_id
                 WHERE c.property_id = p.id AND c.removed_at IS NULL) AS candidate_agencies,
              (SELECT c.source_url FROM property_sources c WHERE c.property_id = p.id AND c.removed_at IS NULL LIMIT 1) AS candidate_url
       FROM review_queue r JOIN property_sources s ON s.id = r.source_id JOIN properties p ON p.id = r.candidate_property_id
       WHERE ${live} ORDER BY r.score DESC, r.created_at LIMIT 50`
    ).bind(status).all<any>()
    return respond({ total: total?.n ?? 0, items: (results || []).map(r => ({ ...r, reasons: JSON.parse(r.reasons) })) })
  }

  // POST /pipeline/review/:id {decision: 'merge' | 'reject'}
  const reviewMatch = path.match(/^\/pipeline\/review\/([^/]+)$/)
  if (reviewMatch && req.method === 'POST') {
    const { decision } = await req.json<{ decision: string }>()
    if (decision !== 'merge' && decision !== 'reject') return respond({ error: "decision must be 'merge' or 'reject'" }, 400)
    const item = await ctx.db.prepare('SELECT * FROM review_queue WHERE id = ?').bind(reviewMatch[1]).first<any>()
    if (!item) return respond({ error: 'Not found' }, 404)
    if (item.status !== 'pending') return respond({ error: `Already ${item.status}` }, 409)
    return respond(await decideReview(ctx, item, decision, agentId, null))
  }

  return respond({ error: 'Not found' }, 404)
}

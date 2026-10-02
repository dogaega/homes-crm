// CRM views over the aggregated listings: browse/filter, one property with
// every agency's listing, hero images, "contacted" marks, and client
// requests (Mark's WORK FRANCE sheet + manual ones) matched to listings.
// Routed from handlePipelineUi (logged-in agent).

import type { Env } from './index'

type Respond = (data: unknown, status?: number) => Response
type CrmEnv = Env & { REQUESTS_SHEET_CSV_URL?: string }

// Towns in scope (local-pipeline/scraper/daily.py SCOPE_MIN_SALE); a few
// properties elsewhere came in before the scope existed and stay hidden.
const SCOPE_CITIES = ['Monaco', 'Beausoleil', 'Roquebrune-Cap-Martin', 'Menton', "Cap-d'Ail", 'La Turbie', 'Èze',
  'Beaulieu-sur-Mer', 'Villefranche-sur-Mer', 'Saint-Jean-Cap-Ferrat', 'Nice', 'Cannes', 'Antibes', 'Saint-Tropez',
  'Ramatuelle', 'Gassin']
const LIVE = `p.origin = 'pipeline' AND p.merged_into IS NULL AND p.listing_status = 'active'
  AND p.city IN (${SCOPE_CITIES.map(c => `'${c.replace(/'/g, "''")}'`).join(',')})`

// Everything first seen before daily runs started is the initial import, not "new".
const NEW_BASELINE = '2026-10-03T00:00:00Z'
export const newSince = () => {
  const week = new Date(Date.now() - 7 * 86400_000).toISOString()
  return week > NEW_BASELINE ? week : NEW_BASELINE
}
const VILLA = `(LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*villa*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*maison*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*house*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*propri*t*')`
const COMMERCIAL = `(LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*bureau*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*office*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*commerc*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*local*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*boutique*'
  OR LOWER(COALESCE(p.property_type, '') || ' ' || COALESCE(p.property_name, '')) GLOB '*shop*')`
const LIST_COLS = `p.id, p.property_id, p.property_name, p.price, p.transaction_type, p.city, p.quarter, p.building_name,
  p.bedrooms, p.room_count, p.living_area_sqm, p.terrace_sqm, p.floor, p.sea_view, p.property_type, p.hero_image_key,
  p.source_count, p.first_seen_at, p.last_seen_at, p.pipeline_status,
  (SELECT COUNT(*) FROM contact_marks c WHERE c.property_id = p.id) AS contact_count,
  (SELECT json_extract(s.photo_urls, '$[0]') FROM property_sources s
   WHERE s.property_id = p.id AND s.removed_at IS NULL AND s.photo_urls LIKE '["http%' LIMIT 1) AS photo_url`

// ── requests: criteria ───────────────────────────────────────────────────

export interface Criteria {
  transaction_type: 'sale' | 'rent'
  cities: string[]
  quarters: string[]            // Monaco quarters (ids) when the request names one
  kind: 'apartment' | 'villa' | 'any'
  commercial: boolean
  bedrooms_min: number | null
  bedrooms_max: number | null
  area_min: number | null
  area_max: number | null
  price_max: number | null      // sale budget or monthly rent budget
  location_text: string
  notes: string
}

// Town names as Mark's team writes them (RU / EN / FR) → canonical city.
const CITY_ALIASES: [RegExp, string[]][] = [
  [/монак|monaco|монте[- ]?карл|monte[- ]?carl/i, ['Monaco']],
  [/границ|border|frontière/i, ['Beausoleil', "Cap-d'Ail", 'Roquebrune-Cap-Martin']],
  [/босол|beausoleil/i, ['Beausoleil']],
  [/кап[- ]?март|рокен?брюн|рокнбрюн|roquebrune|cap[- ]?martin/i, ['Roquebrune-Cap-Martin']],
  [/кап[- ]?д.?ай|cap[- ]?d.?ail/i, ["Cap-d'Ail"]],
  [/ментон|минтон|menton/i, ['Menton']],
  [/эз[еа]?\b|\b[eè]ze\b/i, ['Èze']],
  [/больё|болье|beaulieu/i, ['Beaulieu-sur-Mer']],
  [/вильфранш|villefranche/i, ['Villefranche-sur-Mer']],
  [/кап[- ]?ферр?а|ферра|ferrat/i, ['Saint-Jean-Cap-Ferrat']],
  [/ницц|\bnice\b/i, ['Nice']],
  [/канн|cannes/i, ['Cannes']],
  [/антиб|antibes|juan/i, ['Antibes']],
  [/тропе|tropez|tropetz|рамату|ramatuelle|гассен|gassin/i, ['Saint-Tropez', 'Ramatuelle', 'Gassin']],
]
const QUARTER_ALIASES: [RegExp, string][] = [
  [/carr[ée] d.or|карре|monte[- ]?carlo|монте[- ]?карло/i, 'monte-carlo'], [/larvotto|ларвотт/i, 'larvotto'], [/fontvieille|фонвьей/i, 'fontvieille'],
  [/condamine|кондамин/i, 'la-condamine'], [/moneghetti|монегетти/i, 'moneghetti'], [/mareterra|маретерра|jardins d.eau/i, 'mareterra'],
]

const num = (s: string): number | null => {
  const v = parseFloat(s.replace(/[\s  ]/g, '').replace(',', '.'))
  return Number.isFinite(v) ? v : null
}

export function parseCriteria(r: { type1?: string; type2?: string; location?: string; bedrooms?: string; area?: string;
                                   price?: string; rent?: string; notes?: string }): Criteria {
  const loc = r.location || ''
  const text = [r.type2, loc, r.bedrooms, r.notes].join(' ')
  let cities = [...new Set(CITY_ALIASES.flatMap(([rx, c]) => (rx.test(loc) ? c : [])))]
  let quarters = [...new Set(QUARTER_ALIASES.flatMap(([rx, q]) => (rx.test(loc) ? [q] : [])))]
  // "Граница Франция Монако" = the French towns on the border, not Monaco itself.
  if (/границ|border|frontière/i.test(loc)) cities = cities.filter(c => c !== 'Monaco')
  // A Monaco quarter named next to another town ("Moneghetti is too far") doesn't add Monaco.
  if (quarters.length && !cities.length) cities = ['Monaco']
  else if (quarters.length && !cities.includes('Monaco')) quarters = []
  const beds = `${r.bedrooms || ''} ${loc}`.match(/(\d+)(?:\s*[-–\s]\s*(\d+))?\s*(?:спал|bed|chamb)/i)
  const area = (r.area || '').match(/(\d+)(?:\s*[-–]\s*(\d+))?/)
  const sale = r.price ? num(r.price) : null
  const rent = r.rent ? num(r.rent) : null
  const isRent = rent != null || (sale == null && /аренд|rent|location/i.test(text))
  const type2 = (r.type2 || '').toLowerCase()
  return {
    transaction_type: isRent ? 'rent' : 'sale',
    cities, quarters,
    kind: /вилл|villa|дом|house|maison/.test(type2) ? 'villa' : /кварт|apart|flat|пентх|penthouse|студ/.test(type2) ? 'apartment' : 'any',
    commercial: /н\/ф/i.test(r.type1 || '') || /салон|офис|office|commerc|бутик|магаз/i.test(type2),
    bedrooms_min: beds ? +beds[1] : null,
    bedrooms_max: beds ? +(beds[2] || beds[1]) : null,
    area_min: area ? (area[2] ? +area[1] : Math.round(+area[1] * 0.85)) : null,
    area_max: area?.[2] ? +area[2] : null,
    price_max: isRent ? rent : sale,
    location_text: loc,
    notes: [r.bedrooms && !beds ? r.bedrooms : '', r.notes || ''].filter(Boolean).join(' · '),
  }
}

// ── requests: Google Sheet sync ──────────────────────────────────────────

function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = [], cell = '', q = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++ }
      else if (ch === '"') q = false
      else cell += ch
    } else if (ch === '"') q = true
    else if (ch === ',') { row.push(cell); cell = '' }
    else if (ch === '\n') { row.push(cell); rows.push(row); row = []; cell = '' }
    else if (ch !== '\r') cell += ch
  }
  if (cell || row.length) { row.push(cell); rows.push(row) }
  return rows
}

async function sha(s: string): Promise<string> {
  const d = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(s))
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 16)
}

// Re-reads the sheet when the last sync is older than 10 minutes. Rows are
// keyed by their content (client + phone + criteria), so editing a row
// replaces that request and deleting it removes it here too.
async function syncSheet(env: CrmEnv, force = false): Promise<{ synced: boolean; error?: string }> {
  if (!env.REQUESTS_SHEET_CSV_URL) return { synced: false, error: 'REQUESTS_SHEET_CSV_URL not set' }
  const last = await env.DB.prepare(
    `SELECT MAX(updated_at) AS t FROM saved_searches WHERE json_extract(criteria, '$.source') = 'sheet'`
  ).first<{ t: string | null }>()
  if (!force && last?.t && Date.now() - Date.parse(last.t) < 10 * 60_000) return { synced: false }
  const res = await fetch(env.REQUESTS_SHEET_CSV_URL, { redirect: 'follow' })
  if (!res.ok) return { synced: false, error: `sheet HTTP ${res.status}` }
  const rows = parseCsv(await res.text())
  const hdr = rows.findIndex(r => r.some(c => /^Клиент$/i.test(c.trim())))
  if (hdr < 0) return { synced: false, error: 'header row (Клиент) not found' }
  const col = (name: RegExp) => rows[hdr].findIndex(c => name.test(c.trim()))
  const C = {
    comment: col(/^Комментарий$/), brokerNote: col(/^Комментарий брокера/), broker: col(/^Брокер/), client: col(/^Клиент/),
    middleman: col(/^Посредник/), phone: col(/^Номер клиента/), middlemanPhone: col(/^Номер посредника/),
    location: col(/^Локация/), bedrooms: col(/спален/), area: col(/^Площадь/), price: col(/^Цена/), rent: col(/^Аренда/),
    deadline: col(/^Дедлайн/), status: col(/^Статус/),
  }
  const typeCols = rows[hdr].map((c, i) => (/^Тип объекта/.test(c.trim()) ? i : -1)).filter(i => i >= 0)
  const now = new Date().toISOString()
  const at = (r: string[], i: number) => (i >= 0 ? (r[i] || '').trim() : '')
  const seen: string[] = []
  for (const r of rows.slice(hdr + 1)) {
    const fields = {
      type1: at(r, typeCols[0] ?? -1), type2: at(r, typeCols[1] ?? -1), location: at(r, C.location),
      bedrooms: at(r, C.bedrooms), area: at(r, C.area), price: at(r, C.price), rent: at(r, C.rent),
      notes: [at(r, C.comment), at(r, C.brokerNote)].filter(Boolean).join(' · '),
    }
    if (!fields.location && !fields.price && !fields.rent && !fields.bedrooms) continue
    const client = at(r, C.client) || at(r, C.middleman) || 'Client'
    const key = await sha(JSON.stringify([client, at(r, C.phone), fields]))
    seen.push(key)
    const criteria = {
      ...parseCriteria(fields), source: 'sheet', sheet_key: key,
      client_name: client, client_phone: at(r, C.phone) || null, middleman: at(r, C.middleman) || null,
      middleman_phone: at(r, C.middlemanPhone) || null, broker: at(r, C.broker) || null,
      deadline: at(r, C.deadline) || null, status: at(r, C.status) || null, raw: fields,
    }
    const exists = await env.DB.prepare(
      `SELECT id FROM saved_searches WHERE json_extract(criteria, '$.sheet_key') = ?`
    ).bind(key).first<{ id: string }>()
    if (exists) {
      await env.DB.prepare(`UPDATE saved_searches SET updated_at = ?, active = 1 WHERE id = ?`).bind(now, exists.id).run()
    } else {
      await env.DB.prepare(
        `INSERT INTO saved_searches (id, name, criteria, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)`
      ).bind(crypto.randomUUID(), client, JSON.stringify(criteria), now, now).run()
    }
  }
  // Rows gone from the sheet (deleted or edited) go here too.
  const { results } = await env.DB.prepare(
    `SELECT id, json_extract(criteria, '$.sheet_key') AS k FROM saved_searches WHERE json_extract(criteria, '$.source') = 'sheet'`
  ).all<{ id: string; k: string }>()
  for (const r of results || []) {
    if (!seen.includes(r.k)) await env.DB.prepare('DELETE FROM saved_searches WHERE id = ?').bind(r.id).run()
  }
  return { synced: true }
}

// ── matching ─────────────────────────────────────────────────────────────

function matchWhere(c: Criteria): { where: string[]; binds: unknown[] } {
  const where = [LIVE, 'p.transaction_type = ?']
  const binds: unknown[] = [c.transaction_type]
  if (c.cities.length) { where.push(`p.city IN (${c.cities.map(() => '?').join(',')})`); binds.push(...c.cities) }
  if (c.quarters.length && c.cities.length === 1 && c.cities[0] === 'Monaco') {
    where.push(`p.quarter IN (${c.quarters.map(() => '?').join(',')})`); binds.push(...c.quarters)
  }
  if (c.price_max) {
    // Budget is a ceiling with ~10% room to negotiate; far below it is not what they're after.
    where.push('(p.price IS NULL OR p.price BETWEEN ? AND ?)'); binds.push(Math.round(c.price_max * 0.4), Math.round(c.price_max * 1.1))
  }
  if (c.bedrooms_min != null) { where.push('(p.bedrooms IS NULL OR p.bedrooms >= ?)'); binds.push(c.bedrooms_min) }
  if (c.bedrooms_max != null) { where.push('(p.bedrooms IS NULL OR p.bedrooms <= ?)'); binds.push(c.bedrooms_max + 1) }
  if (c.area_min) { where.push('(p.living_area_sqm IS NULL OR p.living_area_sqm >= ?)'); binds.push(c.area_min) }
  if (c.area_max) { where.push('(p.living_area_sqm IS NULL OR p.living_area_sqm <= ?)'); binds.push(Math.round(c.area_max * 1.15)) }
  where.push(c.commercial ? COMMERCIAL : `NOT ${COMMERCIAL}`)
  if (c.kind === 'villa') where.push(VILLA)
  if (c.kind === 'apartment') where.push(`NOT ${VILLA}`)
  return { where, binds }
}

// Match counts for the request list — one D1 round trip for all requests.
async function matchCounts(env: Env, list: Criteria[]): Promise<{ total: number; fresh: number }[]> {
  if (!list.length) return []
  const since = newSince()
  const res = await env.DB.batch(list.map(c => {
    const { where, binds } = matchWhere(c)
    return env.DB.prepare(
      `SELECT COUNT(*) AS total, SUM(p.first_seen_at >= ?) AS fresh FROM properties p WHERE ${where.join(' AND ')}`
    ).bind(since, ...binds)
  }))
  return res.map(r => {
    const row = (r.results?.[0] || {}) as { total?: number; fresh?: number | null }
    return { total: row.total ?? 0, fresh: row.fresh ?? 0 }
  })
}

async function matches(env: Env, c: Criteria, limit = 60): Promise<any[]> {
  const { where, binds } = matchWhere(c)
  // Score on a light pull, then load the full card columns for the winners only.
  const { results } = await env.DB.prepare(
    `SELECT p.id, p.price, p.bedrooms, p.living_area_sqm, p.hero_image_key, p.source_count, p.first_seen_at
     FROM properties p WHERE ${where.join(' AND ')} ORDER BY p.first_seen_at DESC LIMIT 1000`
  ).bind(...binds).all<any>()
  const since = newSince()
  const top = (results || []).map(p => {
    let score = 50
    if (c.price_max && p.price) score += 25 - Math.min(25, Math.abs(1 - p.price / c.price_max) * 50)
    if (c.price_max && p.price == null) score -= 10
    if (c.bedrooms_min != null) score += p.bedrooms == null ? -5 : p.bedrooms <= (c.bedrooms_max ?? p.bedrooms) ? 10 : 3
    if (c.area_min) score += p.living_area_sqm == null ? -5 : 5
    if (p.hero_image_key) score += 5
    if (p.source_count > 1) score += 2
    return { id: p.id as string, score: Math.round(score), first_seen_at: p.first_seen_at as string }
  }).sort((a, b) => b.score - a.score || (b.first_seen_at > a.first_seen_at ? 1 : -1)).slice(0, limit)
  if (!top.length) return []
  const { results: cards } = await env.DB.prepare(
    `SELECT ${LIST_COLS} FROM properties p WHERE p.id IN (${top.map(() => '?').join(',')})`
  ).bind(...top.map(t => t.id)).all<any>()
  const byId = new Map((cards || []).map(x => [x.id, x]))
  return top.flatMap(t => (byId.has(t.id) ? [{ ...byId.get(t.id), score: t.score, is_new: t.first_seen_at >= since }] : []))
}

// ── routes ───────────────────────────────────────────────────────────────

export async function handleCrm(req: Request, env: CrmEnv, url: URL, path: string, agentId: string | null,
                                respond: Respond): Promise<Response | null> {
  const q = url.searchParams

  // GET /pipeline/hero/<r2 key> — hero images (session-protected, cached by the browser).
  if (path.startsWith('/pipeline/hero/') && req.method === 'GET') {
    const key = decodeURIComponent(path.slice('/pipeline/hero/'.length))
    if (!/^[\w./-]+$/.test(key) || key.includes('..')) return respond({ error: 'Bad key' }, 400)
    const obj = await env.DOCS.get(key)
    if (!obj) return respond({ error: 'Not found' }, 404)
    return new Response(obj.body, { headers: { 'Content-Type': obj.httpMetadata?.contentType || 'image/webp',
                                               'Cache-Control': 'private, max-age=604800' } })
  }

  // GET /pipeline/listings?city=&tx=&min=&max=&beds=&area=&q=&days=&sort=&page=
  if (path === '/pipeline/listings' && req.method === 'GET') {
    const where = [LIVE]; const binds: unknown[] = []
    const cities = q.getAll('city').filter(Boolean)
    if (cities.length) { where.push(`p.city IN (${cities.map(() => '?').join(',')})`); binds.push(...cities) }
    if (q.get('tx')) { where.push('p.transaction_type = ?'); binds.push(q.get('tx')) }
    if (q.get('min')) { where.push('p.price >= ?'); binds.push(+q.get('min')!) }
    if (q.get('max')) { where.push('p.price <= ?'); binds.push(+q.get('max')!) }
    if (q.get('beds')) { where.push('p.bedrooms >= ?'); binds.push(+q.get('beds')!) }
    if (q.get('area')) { where.push('p.living_area_sqm >= ?'); binds.push(+q.get('area')!) }
    if (q.get('days')) { where.push('p.first_seen_at >= ?'); binds.push(new Date(Date.now() - +q.get('days')! * 86400_000).toISOString()) }
    if (q.get('type') === 'villa') where.push(VILLA)
    if (q.get('type') === 'apartment') where.push(`NOT ${VILLA}`)
    if (q.get('q')) {
      where.push(`(p.property_name LIKE ? OR p.building_name LIKE ? OR p.quarter LIKE ? OR p.property_id LIKE ?)`)
      binds.push(...Array(4).fill(`%${q.get('q')}%`))
    }
    const order = ({ price_asc: 'p.price IS NULL, p.price', price_desc: 'p.price DESC', area: 'p.living_area_sqm DESC' } as Record<string, string>)[q.get('sort') || ''] || 'p.first_seen_at DESC'
    const page = Math.max(0, +(q.get('page') || 0)); const size = 30
    const total = await env.DB.prepare(`SELECT COUNT(*) AS n FROM properties p WHERE ${where.join(' AND ')}`).bind(...binds).first<{ n: number }>()
    const { results } = await env.DB.prepare(
      `SELECT ${LIST_COLS} FROM properties p WHERE ${where.join(' AND ')} ORDER BY ${order} LIMIT ? OFFSET ?`
    ).bind(...binds, size, page * size).all<any>()
    const since = newSince()
    return respond({ total: total?.n ?? 0, page, page_size: size,
                     items: (results || []).map(x => ({ ...x, is_new: x.first_seen_at >= since })) })
  }

  // GET /pipeline/cities — towns with live listings, for the filter.
  if (path === '/pipeline/cities' && req.method === 'GET') {
    const { results } = await env.DB.prepare(
      `SELECT p.city, COUNT(*) AS n FROM properties p WHERE ${LIVE} GROUP BY p.city ORDER BY n DESC`).all<any>()
    return respond(results || [])
  }

  // GET /pipeline/listings/:id — the property, every agency's listing, price history, contact marks.
  const one = path.match(/^\/pipeline\/listings\/([^/]+)$/)
  if (one && req.method === 'GET') {
    const p = await env.DB.prepare(`SELECT * FROM properties WHERE id = ? AND origin = 'pipeline'`).bind(one[1]).first<any>()
    if (!p) return respond({ error: 'Not found' }, 404)
    const { results: sources } = await env.DB.prepare(
      `SELECT s.id, s.source_url, s.site_key, s.listing_title, s.listing_description, s.price_at_source, s.price_on_request,
              s.currency, s.rent_period, s.transaction_type, s.bedrooms, s.rooms, s.living_area_sqm, s.floor, s.external_ref,
              s.agent_name, s.agent_phone, s.agent_email, s.agent_whatsapp, s.agency_phone, s.agency_email, s.photo_urls,
              s.hero_image_key, s.first_seen_at, s.last_seen_at, s.removed_at,
              a.name AS agency_name, a.phone AS agency_main_phone, a.email AS agency_main_email, a.website AS agency_website
       FROM property_sources s LEFT JOIN agencies a ON a.id = s.agency_id
       WHERE s.property_id = ? ORDER BY s.removed_at IS NOT NULL, s.price_at_source`
    ).bind(p.id).all<any>()
    const { results: history } = await env.DB.prepare(
      `SELECT h.source_id, h.price, h.previous_price, h.observed_at FROM price_history h WHERE h.property_id = ? ORDER BY h.observed_at`
    ).bind(p.id).all<any>()
    const { results: marks } = await env.DB.prepare(
      `SELECT c.*, ag.agent_name AS marked_by_name FROM contact_marks c LEFT JOIN agents ag ON ag.id = c.marked_by
       WHERE c.property_id = ? ORDER BY c.created_at DESC`
    ).bind(p.id).all<any>()
    return respond({ property: p, sources: (sources || []).map(s => ({ ...s, photo_urls: s.photo_urls ? JSON.parse(s.photo_urls) : [] })),
                     price_history: history || [], contact_marks: marks || [] })
  }

  // POST /pipeline/listings/:id/contact {source_id?, note?} — "contacted this agency".
  const mark = path.match(/^\/pipeline\/listings\/([^/]+)\/contact$/)
  if (mark && req.method === 'POST') {
    const body = await req.json<{ source_id?: string; note?: string }>().catch(() => ({} as { source_id?: string; note?: string }))
    const src = body.source_id ? await env.DB.prepare('SELECT agency_id FROM property_sources WHERE id = ? AND property_id = ?')
      .bind(body.source_id, mark[1]).first<{ agency_id: string }>() : null
    const id = crypto.randomUUID()
    await env.DB.prepare(
      `INSERT INTO contact_marks (id, property_id, agency_id, source_id, marked_by, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)`
    ).bind(id, mark[1], src?.agency_id ?? null, body.source_id ?? null, agentId, body.note?.slice(0, 1000) ?? null, new Date().toISOString()).run()
    await env.DB.prepare(`UPDATE properties SET pipeline_status = 'contacted' WHERE id = ? AND pipeline_status = 'uncontacted'`).bind(mark[1]).run()
    return respond({ id }, 201)
  }

  // GET /pipeline/requests — sheet + manual requests, each with its match count.
  if (path === '/pipeline/requests' && req.method === 'GET') {
    const sync = await syncSheet(env, q.get('sync') === '1')
    const { results } = await env.DB.prepare(
      `SELECT * FROM saved_searches WHERE active = 1 ORDER BY json_extract(criteria, '$.source') DESC, created_at`
    ).all<any>()
    const rows = (results || []).map(r => ({ ...r, criteria: JSON.parse(r.criteria) }))
    const counts = await matchCounts(env, rows.map(r => r.criteria))
    const out = rows.map((r, i) => ({ id: r.id, name: r.name, criteria: r.criteria, created_at: r.created_at,
                                      match_count: counts[i].total, new_count: counts[i].fresh }))
    return respond({ sync, items: out })
  }

  // GET /pipeline/requests/:id/matches
  const rm = path.match(/^\/pipeline\/requests\/([^/]+)\/matches$/)
  if (rm && req.method === 'GET') {
    const r = await env.DB.prepare('SELECT * FROM saved_searches WHERE id = ?').bind(rm[1]).first<any>()
    if (!r) return respond({ error: 'Not found' }, 404)
    return respond({ id: r.id, name: r.name, criteria: JSON.parse(r.criteria), items: await matches(env, JSON.parse(r.criteria)) })
  }

  // POST /pipeline/requests — manual request {client_name, client_phone?, type?, location, bedrooms?, area?, price?, rent?, notes?}
  if (path === '/pipeline/requests' && req.method === 'POST') {
    const b = await req.json<Record<string, string>>()
    if (!b.client_name?.trim()) return respond({ error: 'client_name is required' }, 400)
    const fields = { type1: b.commercial ? 'н/ф' : 'ж/ф', type2: b.type || '', location: b.location || '', bedrooms: b.bedrooms || '',
                     area: b.area || '', price: b.price || '', rent: b.rent || '', notes: b.notes || '' }
    const criteria = { ...parseCriteria(fields), source: 'manual', client_name: b.client_name.trim(),
                       client_phone: b.client_phone || null, raw: fields }
    const id = crypto.randomUUID(); const now = new Date().toISOString()
    await env.DB.prepare(
      `INSERT INTO saved_searches (id, agent_id, name, criteria, active, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)`
    ).bind(id, agentId, criteria.client_name, JSON.stringify(criteria), now, now).run()
    return respond({ id, criteria }, 201)
  }

  // DELETE /pipeline/requests/:id — manual requests only (sheet rows are edited in the sheet).
  const rd = path.match(/^\/pipeline\/requests\/([^/]+)$/)
  if (rd && req.method === 'DELETE') {
    const r = await env.DB.prepare('SELECT criteria FROM saved_searches WHERE id = ?').bind(rd[1]).first<{ criteria: string }>()
    if (!r) return respond({ error: 'Not found' }, 404)
    if (JSON.parse(r.criteria).source === 'sheet') return respond({ error: 'Edit or remove this request in the sheet' }, 409)
    await env.DB.prepare('DELETE FROM saved_searches WHERE id = ?').bind(rd[1]).run()
    return respond({ deleted: true })
  }

  return null
}

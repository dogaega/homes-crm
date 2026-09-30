// Monaco Riviera CRM — Cloudflare Worker API backed by D1.
// Replaces Supabase Postgres + Supabase Auth.
//
// Auth: email+password, PBKDF2 (Web Crypto) password hashing, opaque session
// token stored in an httpOnly cookie and mirrored in a `sessions` table.
//
// Transport: the Next.js dev/prod server proxies `/api/backend/*` to this
// Worker (see next.config.ts rewrites), so browser requests are same-origin
// and the httpOnly session cookie just works with SameSite=Lax — no CORS
// dance needed for the primary path. CORS headers are still emitted for
// direct-to-worker calls (e.g. curl, tests) using ALLOWED_ORIGINS.

import puppeteer from '@cloudflare/puppeteer'
import { PDFDocument } from 'pdf-lib'
import { renderBrochureHtml, brochureFilename, type Lang } from './brochure'

export interface Env {
  DB: D1Database
  DOCS: R2Bucket
  BROWSER: Fetcher
  ALLOWED_ORIGINS: string
  SIGNUP_INVITE_CODE: string
  GROQ_API_KEY: string
  GROQ_API_KEYS: string
  NVIDIA_API_KEY: string
  // Shared secret for the local scraper pipeline (Bearer token on /sync/*).
  // Set via `wrangler secret put SYNC_API_TOKEN` — never checked in.
  SYNC_API_TOKEN: string
}

const TABLES = [
  'agents', 'clients', 'properties', 'tasks', 'task_comments',
  'task_templates', 'documents', 'document_templates', 'document_signatures',
  'communications', 'showings', 'inquiries', 'client_property_interests',
  'activity_logs', 'agencies', 'agency_contacts', 'intake_documents',
  'property_sources',
] as const
type TableName = typeof TABLES[number]

const JSON_COLUMNS: Record<string, string[]> = {
  agents: ['performance_metrics', 'social_media', 'territory', 'specialties'],
  clients: ['budget_range', 'preferences', 'tags'],
  properties: ['features', 'photos', 'construction_details', 'engineering_details', 'room_layout', 'floor_plan_urls', 'gallery_urls'],
  tasks: [],
  task_templates: ['tasks'],
  documents: ['field_values', 'tags'],
  document_templates: ['template_fields'],
  inquiries: ['preferred_locations'],
  activity_logs: ['metadata'],
  agencies: [],
  agency_contacts: [],
  intake_documents: ['extracted_fields'],
  property_sources: [],
}

function corsHeaders(origin: string | null, env: Env) {
  const allowed = env.ALLOWED_ORIGINS.split(',').map(s => s.trim())
  const allowOrigin = origin && allowed.includes(origin) ? origin : allowed[0]
  return {
    'Access-Control-Allow-Origin': allowOrigin,
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
  }
}

function json(data: unknown, status = 200, extraHeaders: Record<string, string> = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  })
}

// ── password hashing (PBKDF2 via Web Crypto) ──────────────────────────
async function hashPassword(password: string, saltHex?: string) {
  const salt = saltHex
    ? new Uint8Array(saltHex.match(/.{2}/g)!.map(b => parseInt(b, 16)))
    : crypto.getRandomValues(new Uint8Array(16))
  const keyMaterial = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']
  )
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt, iterations: 100_000, hash: 'SHA-256' },
    keyMaterial, 256
  )
  const hashHex = [...new Uint8Array(bits)].map(b => b.toString(16).padStart(2, '0')).join('')
  const saltHexOut = [...salt].map(b => b.toString(16).padStart(2, '0')).join('')
  return { hash: hashHex, salt: saltHexOut }
}

async function verifyPassword(password: string, hash: string, salt: string) {
  const { hash: computed } = await hashPassword(password, salt)
  return computed === hash
}

function newToken() {
  return [...crypto.getRandomValues(new Uint8Array(32))].map(b => b.toString(16).padStart(2, '0')).join('')
}

function getCookie(req: Request, name: string): string | null {
  const cookie = req.headers.get('Cookie') || ''
  const match = cookie.match(new RegExp(`(?:^|; )${name}=([^;]+)`))
  return match ? decodeURIComponent(match[1]) : null
}

function sessionCookie(token: string, maxAgeSec: number) {
  return `session=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAgeSec}`
}

async function getSessionUser(req: Request, env: Env) {
  const token = getCookie(req, 'session') || (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '')
  if (!token) return null
  const row = await env.DB.prepare(
    `SELECT u.id, u.email, u.full_name, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?`
  ).bind(token).first<any>()
  if (!row) return null
  if (new Date(row.expires_at).getTime() < Date.now()) return null
  return { id: row.id, email: row.email, full_name: row.full_name }
}

function parseJsonCols(table: string, row: any) {
  const cols = JSON_COLUMNS[table] || []
  for (const c of cols) {
    if (row[c] != null && typeof row[c] === 'string') {
      try { row[c] = JSON.parse(row[c]) } catch { /* leave as-is */ }
    }
  }
  return row
}

function stringifyJsonCols(table: string, body: Record<string, any>) {
  const cols = JSON_COLUMNS[table] || []
  const out = { ...body }
  for (const c of cols) {
    if (out[c] != null && typeof out[c] !== 'string') out[c] = JSON.stringify(out[c])
  }
  // arrays that aren't declared JSON columns (e.g. text[] in pg) get JSON-encoded too
  for (const k of Object.keys(out)) {
    if (Array.isArray(out[k])) out[k] = JSON.stringify(out[k])
  }
  return out
}

async function handleAuth(req: Request, env: Env, path: string, origin: string | null): Promise<Response> {
  const headers = corsHeaders(origin, env)

  if (path === '/auth/signup' && req.method === 'POST') {
    const { email, password, full_name, invite_code } = await req.json<any>()
    if (!email || !password) return json({ error: 'email and password required' }, 400, headers)
    if (!env.SIGNUP_INVITE_CODE || invite_code !== env.SIGNUP_INVITE_CODE) {
      return json({ error: 'Invalid or missing invite code' }, 403, headers)
    }
    const existing = await env.DB.prepare('SELECT id FROM users WHERE email = ?').bind(email).first()
    if (existing) return json({ error: 'User already exists' }, 409, headers)

    const { hash, salt } = await hashPassword(password)
    const userId = crypto.randomUUID()
    const now = new Date().toISOString()
    await env.DB.prepare(
      'INSERT INTO users (id, email, password_hash, password_salt, full_name, created_at, updated_at) VALUES (?,?,?,?,?,?,?)'
    ).bind(userId, email, hash, salt, full_name || null, now, now).run()

    // Mirror Supabase AuthContext.fetchAgentData: create an agents row for the new user
    const agentId = crypto.randomUUID()
    await env.DB.prepare(
      'INSERT INTO agents (id, user_id, agent_name, email, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)'
    ).bind(agentId, userId, full_name || email.split('@')[0], email, 'active', now, now).run()

    const token = newToken()
    const expires = new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString()
    await env.DB.prepare('INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)')
      .bind(token, userId, now, expires).run()

    return json({ user: { id: userId, email, full_name }, token }, 200, {
      ...headers, 'Set-Cookie': sessionCookie(token, 30 * 24 * 3600),
    })
  }

  if (path === '/auth/login' && req.method === 'POST') {
    const { email, password } = await req.json<any>()
    const user = await env.DB.prepare('SELECT * FROM users WHERE email = ?').bind(email).first<any>()
    if (!user) return json({ error: 'Invalid credentials' }, 401, headers)
    const ok = await verifyPassword(password, user.password_hash, user.password_salt)
    if (!ok) return json({ error: 'Invalid credentials' }, 401, headers)

    const token = newToken()
    const now = new Date().toISOString()
    const expires = new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString()
    await env.DB.prepare('INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)')
      .bind(token, user.id, now, expires).run()

    return json({ user: { id: user.id, email: user.email, full_name: user.full_name }, token }, 200, {
      ...headers, 'Set-Cookie': sessionCookie(token, 30 * 24 * 3600),
    })
  }

  if (path === '/auth/logout' && req.method === 'POST') {
    const token = getCookie(req, 'session')
    if (token) await env.DB.prepare('DELETE FROM sessions WHERE token = ?').bind(token).run()
    return json({ ok: true }, 200, { ...headers, 'Set-Cookie': sessionCookie('', 0) })
  }

  if (path === '/auth/session' && req.method === 'GET') {
    const user = await getSessionUser(req, env)
    return json({ user }, 200, headers)
  }

  if (path === '/auth/reset-password' && req.method === 'POST') {
    // Email delivery is out of scope for this migration — clear stub.
    return json({ error: 'Password reset via email is not implemented yet (501)' }, 501, headers)
  }

  return json({ error: 'Not found' }, 404, headers)
}

const PUBLIC_PROPERTY_COLUMNS = `
  id, property_id, slug, address, city, state, zip_code, district, price, bedrooms, bathrooms,
  square_feet, lot_size, plot_size, year_built, floor_count, property_type,
  description, terrain_description, house_history, concept_description,
  construction_details, engineering_details, room_layout, floor_plan_urls,
  gallery_urls, video_url, photos, map_lat, map_lng, featured, listing_status,
  virtual_tour_url
`

// Public listings show an approximate pin, not the exact address — standard
// real-estate practice so a client can't just show up uninvited, and so the
// exact coordinates aren't sitting in a public, unauthenticated API response
// for anyone to read from the network tab. The shift must happen here, not
// just in the frontend's map render, or the true lat/lng still leaks in the
// JSON. Deterministic per-property (seeded by id) so the pin doesn't jump
// around on every reload, but not reversible to the real point without the
// seed.
function jitterCoords(id: string, lat: number, lng: number): { lat: number; lng: number } {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0
  const angle = (h % 3600) / 3600 * 2 * Math.PI
  const distanceM = 100 + (h % 200) // 100-300m, deterministic per property
  const dLat = (distanceM * Math.cos(angle)) / 111_320
  const dLng = (distanceM * Math.sin(angle)) / (111_320 * Math.cos(lat * Math.PI / 180))
  return { lat: lat + dLat, lng: lng + dLng }
}

function withJitteredCoords(row: any): any {
  if (row.map_lat != null && row.map_lng != null) {
    const { lat, lng } = jitterCoords(row.id, row.map_lat, row.map_lng)
    row.map_lat = lat
    row.map_lng = lng
  }
  return row
}

async function handlePublic(req: Request, env: Env, path: string, origin: string | null): Promise<Response> {
  const headers = corsHeaders(origin, env)
  if (req.method !== 'GET') return json({ error: 'Method not allowed' }, 405, headers)

  if (path === '/public/properties') {
    const { results } = await env.DB.prepare(
      `SELECT ${PUBLIC_PROPERTY_COLUMNS} FROM properties WHERE public_listing = 1 ORDER BY featured DESC, created_at DESC`
    ).all()
    return json((results || []).map((r: any) => withJitteredCoords(parseJsonCols('properties', r))), 200, headers)
  }

  const detailMatch = path.match(/^\/public\/properties\/([^/]+)$/)
  if (detailMatch) {
    const slug = decodeURIComponent(detailMatch[1])
    const row = await env.DB.prepare(
      `SELECT ${PUBLIC_PROPERTY_COLUMNS} FROM properties WHERE slug = ? AND public_listing = 1`
    ).bind(slug).first()
    if (!row) return json({ error: 'Not found' }, 404, headers)
    return json(withJitteredCoords(parseJsonCols('properties', row)), 200, headers)
  }

  // Property/marketing photos an agent has uploaded to our own storage
  // (rather than pasting an external URL) — unlike /documents/file/:key,
  // this is deliberately unauthenticated: property photos are meant to be
  // publicly visible (on the public site, and fetched by the brochure
  // generator's headless-browser render, which has no user session), same
  // R2 bucket as documents, distinct route so private documents stay gated.
  const photoMatch = path.match(/^\/public\/photo\/([^/]+)$/)
  if (photoMatch) {
    const key = decodeURIComponent(photoMatch[1])
    const object = await env.DOCS.get(key)
    if (!object) return json({ error: 'Not found' }, 404, headers)
    const fileHeaders = new Headers(headers)
    fileHeaders.set('Content-Type', object.httpMetadata?.contentType || 'application/octet-stream')
    fileHeaders.set('Cache-Control', 'public, max-age=86400')
    return new Response(object.body, { status: 200, headers: fileHeaders })
  }

  return json({ error: 'Not found' }, 404, headers)
}

async function handleDocumentFiles(req: Request, env: Env, url: URL, path: string, origin: string | null): Promise<Response> {
  const headers = corsHeaders(origin, env)
  const user = await getSessionUser(req, env)
  if (!user) return json({ error: 'Unauthorized' }, 401, headers)

  // Property/marketing photos: same R2 bucket as documents, but the key
  // returned is servable via the unauthenticated /public/photo/:key route
  // (see handlePublic) so it can go straight into a property's photos/
  // gallery_urls array, render on the public site, and be fetched by the
  // brochure generator's headless browser without a session.
  if (path === '/photos/upload' && req.method === 'POST') {
    const filename = url.searchParams.get('filename') || 'photo'
    const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_')
    const key = `photos/${crypto.randomUUID()}-${safeName}`
    const contentType = req.headers.get('Content-Type') || 'application/octet-stream'
    if (!contentType.startsWith('image/')) return json({ error: 'Only image uploads are allowed' }, 400, headers)
    const body = await req.arrayBuffer()
    if (body.byteLength === 0) return json({ error: 'Empty file' }, 400, headers)
    if (body.byteLength > 25 * 1024 * 1024) return json({ error: 'File too large (25MB max)' }, 413, headers)

    await env.DOCS.put(key, body, { httpMetadata: { contentType } })

    return json({
      key,
      filename: safeName,
      size: body.byteLength,
      contentType,
      url: `/public/photo/${encodeURIComponent(key)}`,
    }, 200, headers)
  }

  if (path === '/documents/upload' && req.method === 'POST') {
    const filename = url.searchParams.get('filename') || 'file'
    const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_')
    const key = `${crypto.randomUUID()}-${safeName}`
    const contentType = req.headers.get('Content-Type') || 'application/octet-stream'
    const body = await req.arrayBuffer()
    if (body.byteLength === 0) return json({ error: 'Empty file' }, 400, headers)
    if (body.byteLength > 25 * 1024 * 1024) return json({ error: 'File too large (25MB max)' }, 413, headers)

    await env.DOCS.put(key, body, { httpMetadata: { contentType } })

    return json({
      key,
      filename: safeName,
      size: body.byteLength,
      contentType,
      url: `/documents/file/${encodeURIComponent(key)}`,
    }, 200, headers)
  }

  const fileMatch = path.match(/^\/documents\/file\/([^/]+)$/)
  if (fileMatch && req.method === 'GET') {
    const key = decodeURIComponent(fileMatch[1])
    const object = await env.DOCS.get(key)
    if (!object) return json({ error: 'Not found' }, 404, headers)
    const fileHeaders = new Headers(headers)
    fileHeaders.set('Content-Type', object.httpMetadata?.contentType || 'application/octet-stream')
    fileHeaders.set('Cache-Control', 'private, max-age=3600')
    return new Response(object.body, { status: 200, headers: fileHeaders })
  }

  return json({ error: 'Not found' }, 404, headers)
}

// Property intake pipeline: a PDF/pasted-text listing comes in, we extract
// text (PDF via unpdf, which is pure-JS and runs in the Workers isolate —
// no native deps, unlike pdf-parse/pdfjs's Node build), then Groq turns
// that raw text into structured property fields. Nothing touches the
// `properties` table until a human approves it via /intake/:id/approve.
const EXTRACTION_SYSTEM_PROMPT = `You extract real-estate listing data from raw text (agency PDFs, emails, pasted messages) into strict JSON. Only include fields you are confident about — omit anything not stated or implied by the text, never guess or invent a value.

Return JSON matching this shape exactly (all fields optional, omit unknown ones):
{
  "address": string,        // street/building, as specific as the text allows
  "city": string,
  "zip_code": string,
  "price": number,          // numeric only, no currency symbol
  "rental_type": "sale" | "rent",
  "bedrooms": number,
  "bathrooms": number,
  "square_feet": number,    // the real size in SQUARE METERS (not sqft) — this DB column is misnamed but always holds m²
  "property_type": string,  // e.g. "condo", "villa", "studio"
  "description": string,    // a clean 2-4 sentence summary in the source language
  "agency_name": string,
  "agency_phone": string,
  "agency_email": string,
  "reference_number": string
}

Also return a top-level "confidence": "high" | "medium" | "low" — "high" only if address, price, and size are all explicitly stated; "low" if you had to infer most fields from vague text.`

// Shared low-level Groq call. Bulk scraping/backfill runs do many
// extractions per minute and blow through a single key's per-minute rate
// limit fast — rotate across every key in GROQ_API_KEYS (comma-separated;
// falls back to the single GROQ_API_KEY if unset) and only fall back to
// sleeping if every key is rate-limited on the same attempt.
function groqKeys(env: Env): string[] {
  const keys = (env.GROQ_API_KEYS || '').split(',').map(k => k.trim()).filter(Boolean)
  return keys.length > 0 ? keys : [env.GROQ_API_KEY]
}

async function groqChatJSON(env: Env, systemPrompt: string, userContent: string): Promise<any> {
  const keys = groqKeys(env)
  let res: Response | null = null
  let lastErrorText = ''
  let allKeysRateLimited = true
  for (const key of keys) {
    res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        model: 'openai/gpt-oss-120b',
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userContent.slice(0, 15000) },
        ],
        response_format: { type: 'json_object' },
        temperature: 0,
      }),
    })
    if (res.ok) { allKeysRateLimited = false; break }
    lastErrorText = await res.text().catch(() => '')
    if (res.status !== 429) { allKeysRateLimited = false; break }
  }

  // All Groq keys share one org-wide daily token budget, so rotating keys
  // does nothing once that's exhausted (as opposed to a per-minute limit,
  // where it helps) — fall over to NVIDIA's hosted gpt-oss-20b instead of
  // failing the whole intake document.
  if ((!res || !res.ok) && allKeysRateLimited && env.NVIDIA_API_KEY) {
    res = await fetch('https://integrate.api.nvidia.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${env.NVIDIA_API_KEY}`,
      },
      body: JSON.stringify({
        model: 'openai/gpt-oss-20b',
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userContent.slice(0, 15000) },
        ],
        response_format: { type: 'json_object' },
        temperature: 0,
      }),
    })
    if (!res.ok) lastErrorText = await res.text().catch(() => '')
  }

  if (!res || !res.ok) {
    throw new Error(`Groq/NVIDIA call failed (${res?.status}): ${lastErrorText.slice(0, 300)}`)
  }
  const data = await res.json<any>()
  const content = data.choices?.[0]?.message?.content
  if (!content) throw new Error('Groq returned no content')
  return JSON.parse(content)
}

const DESCRIPTION_CLEAN_PROMPT = `You write short, professional, client-facing property descriptions in Russian for a Monaco/French Riviera real-estate brochure. You will be given raw internal broker notes — these are NEVER shown to clients as-is.

Write a clean 2-4 sentence Russian description covering only: location character, size/layout, condition, and genuine selling points (view, terrace, parking, renovation, etc). Warm but honest, no invented facts.

STRIP COMPLETELY — never include any of these even if present in the source:
- Client names or who the property was "sourced for"
- Budgets, price negotiation status, "awaiting reply", "confirmed available" internal status notes
- Agency names, contacts, commission splits
- Internal qualifiers like "not genuine 1BR", "STUDIO" caveats, "kept for reference", comparisons to other listings
- Anything written as a note-to-self rather than a description of the property

If the source text has no real property information to describe (pure internal chatter), return an empty string.

Return JSON: {"description": "..."}`

async function generateClientDescription(env: Env, rawNotes: string): Promise<string> {
  const result = await groqChatJSON(env, DESCRIPTION_CLEAN_PROMPT, rawNotes)
  return typeof result.description === 'string' ? result.description.trim() : ''
}

async function extractWithGroq(env: Env, rawText: string): Promise<{ fields: Record<string, unknown>; confidence: 'high' | 'medium' | 'low' }> {
  const parsed = await groqChatJSON(env, EXTRACTION_SYSTEM_PROMPT, rawText)
  const { confidence, ...fields } = parsed
  return {
    fields,
    confidence: confidence === 'high' || confidence === 'medium' || confidence === 'low' ? confidence : 'low',
  }
}

async function handleIntake(req: Request, env: Env, url: URL, path: string, origin: string | null): Promise<Response> {
  const headers = corsHeaders(origin, env)
  const user = await getSessionUser(req, env)
  if (!user) return json({ error: 'Unauthorized' }, 401, headers)
  const agent = await env.DB.prepare('SELECT id FROM agents WHERE user_id = ?').bind(user.id).first<{ id: string }>()
  if (!agent) return json({ error: 'Agent not found' }, 404, headers)

  if (path === '/intake/upload' && req.method === 'POST') {
    const sourceType = url.searchParams.get('source_type') || 'manual'
    if (!['pdf', 'email', 'whatsapp', 'url', 'manual'].includes(sourceType)) {
      return json({ error: 'Invalid source_type' }, 400, headers)
    }

    let rawText: string | null = null
    let fileKey: string | null = null
    const contentType = req.headers.get('Content-Type') || ''

    if (sourceType === 'pdf') {
      const body = await req.arrayBuffer()
      if (body.byteLength === 0) return json({ error: 'Empty file' }, 400, headers)
      if (body.byteLength > 25 * 1024 * 1024) return json({ error: 'File too large (25MB max)' }, 413, headers)
      const filename = url.searchParams.get('filename') || 'listing.pdf'
      const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_')
      fileKey = `intake/${crypto.randomUUID()}-${safeName}`
      await env.DOCS.put(fileKey, body, { httpMetadata: { contentType: 'application/pdf' } })

      try {
        const { extractText } = await import('unpdf')
        const { text } = await extractText(new Uint8Array(body), { mergePages: true })
        rawText = text
      } catch (err) {
        return json({ error: `PDF text extraction failed: ${err instanceof Error ? err.message : String(err)}` }, 422, headers)
      }
    } else {
      const body = await req.json<{ raw_text?: string; source_url?: string }>()
      rawText = body.raw_text || null
      if (!rawText && sourceType !== 'url') return json({ error: 'raw_text is required for this source_type' }, 400, headers)
    }

    const id = crypto.randomUUID()
    const now = new Date().toISOString()
    const sourceUrl = url.searchParams.get('source_url') || null
    await env.DB.prepare(
      `INSERT INTO intake_documents (id, source_type, raw_text, file_key, source_url, status, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)`
    ).bind(id, sourceType, rawText, fileKey, sourceUrl, agent.id, now, now).run()

    return json({ id, source_type: sourceType, raw_text: rawText, status: 'pending' }, 201, headers)
  }

  const extractMatch = path.match(/^\/intake\/([^/]+)\/extract$/)
  if (extractMatch && req.method === 'POST') {
    const docId = extractMatch[1]
    const doc = await env.DB.prepare('SELECT * FROM intake_documents WHERE id = ?').bind(docId).first<any>()
    if (!doc) return json({ error: 'Not found' }, 404, headers)
    if (!doc.raw_text) return json({ error: 'No text to extract from (empty raw_text)' }, 400, headers)

    let result
    try {
      result = await extractWithGroq(env, doc.raw_text)
    } catch (err) {
      return json({ error: err instanceof Error ? err.message : String(err) }, 502, headers)
    }

    const now = new Date().toISOString()
    await env.DB.prepare(
      `UPDATE intake_documents SET extracted_fields = ?, extraction_confidence = ?, status = 'extracted', updated_at = ? WHERE id = ?`
    ).bind(JSON.stringify(result.fields), result.confidence, now, docId).run()

    return json({ id: docId, extracted_fields: result.fields, confidence: result.confidence, status: 'extracted' }, 200, headers)
  }

  // Human reviews/edits the extracted fields, then this creates the real
  // property row plus a property_sources record — nothing before this
  // point ever touched `properties`.
  const approveMatch = path.match(/^\/intake\/([^/]+)\/approve$/)
  if (approveMatch && req.method === 'POST') {
    const docId = approveMatch[1]
    const doc = await env.DB.prepare('SELECT * FROM intake_documents WHERE id = ?').bind(docId).first<any>()
    if (!doc) return json({ error: 'Not found' }, 404, headers)

    const body = await req.json<Record<string, any>>()
    const fields = { ...(doc.extracted_fields ? JSON.parse(doc.extracted_fields) : {}), ...body }

    if (!fields.address || !fields.city) {
      return json({ error: 'address and city are required to create a property' }, 400, headers)
    }

    // This CRM's convention (set by the existing data) is city='Monaco'
    // with the actual quarter in `state`/district — but extraction has no
    // way to know that and reasonably returns the quarter as the city
    // (e.g. "Monte-Carlo", "Carre d'Or"). Normalize known Monaco quarters
    // so city stays consistent for both display and the dedup check below.
    const MONACO_QUARTERS = new Set([
      'monte-carlo', 'monte carlo', 'la condamine', 'condamine', "carre d'or",
      "carré d'or", 'moneghetti', 'fontvieille', 'mareterra', 'larvotto',
      'monaco-ville', 'monaco ville', 'la rousse-saint roman', 'la rousse',
      'saint roman', 'jardin exotique', "anse du portier",
    ])
    if (MONACO_QUARTERS.has(String(fields.city).trim().toLowerCase())) {
      fields.state = fields.state || fields.city
      fields.city = 'Monaco'
    }

    const now = new Date().toISOString()

    // Dedup: the same underlying unit shows up under multiple listing pages
    // (co-listed by several agencies, or the aggregator re-lists it) with
    // identical facts each time. Rather than creating a new property row
    // per occurrence, attach the new source to the existing property when
    // city + square_feet + price + bedrooms all match exactly — a
    // coincidental match on all four at once is vanishingly unlikely.
    let propId: string
    let isNewProperty = true
    const addressPrefix = String(fields.address).split(',')[0].trim()
    const dupeMatch = (fields.square_feet != null && fields.price != null)
      ? await env.DB.prepare(
          `SELECT id FROM properties WHERE city = ? AND square_feet = ? AND price = ? AND (bedrooms = ? OR (bedrooms IS NULL AND ? IS NULL)) AND address LIKE ? LIMIT 1`
        ).bind(fields.city, fields.square_feet, fields.price, fields.bedrooms ?? null, fields.bedrooms ?? null, `${addressPrefix}%`).first<{ id: string }>()
      : null

    if (dupeMatch) {
      propId = dupeMatch.id
      isNewProperty = false
    } else {
      propId = crypto.randomUUID()
      const lastProp = await env.DB.prepare(
        `SELECT property_id FROM properties ORDER BY property_id DESC LIMIT 1`
      ).first<{ property_id: string }>()
      const lastNum = lastProp?.property_id?.match(/PROP-(\d+)/)
      const nextPropertyId = `PROP-${((lastNum ? parseInt(lastNum[1]) : 0) + 1).toString().padStart(3, '0')}`

      // `description` stays as-is (the raw intake text — useful internal
      // context for the team), but the brochure only ever reads
      // `concept_description`, which is a Groq-cleaned client-safe rewrite
      // with client names/budgets/agency/negotiation notes stripped out.
      // Never skip this: a raw internal note reaching a client brochure is
      // the actual bug this exists to prevent.
      let clientDescription = ''
      if (fields.description) {
        try {
          clientDescription = await generateClientDescription(env, fields.description)
        } catch (err) {
          console.error('Client description generation failed:', err)
        }
      }

      await env.DB.prepare(
        `INSERT INTO properties (id, property_id, address, city, state, zip_code, price, bedrooms, bathrooms, square_feet, property_type, description, concept_description, listing_status, assigned_agent_id, created_by, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)`
      ).bind(
        propId, nextPropertyId, fields.address, fields.city, fields.state || fields.city, fields.zip_code || '',
        fields.price ?? null, fields.bedrooms ?? null, fields.bathrooms ?? null, fields.square_feet ?? null,
        fields.property_type || null, fields.description || null, clientDescription || null, agent.id, agent.id, now, now
      ).run()
    }

    let agencyId: string | null = null
    if (fields.agency_name) {
      const existing = await env.DB.prepare('SELECT id FROM agencies WHERE name = ?').bind(fields.agency_name).first<{ id: string }>()
      if (existing) {
        agencyId = existing.id
      } else {
        agencyId = crypto.randomUUID()
        await env.DB.prepare(
          `INSERT INTO agencies (id, name, phone, email, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`
        ).bind(agencyId, fields.agency_name, fields.agency_phone || null, fields.agency_email || null, now, now).run()
      }
    }

    await env.DB.prepare(
      `INSERT INTO property_sources (id, property_id, intake_document_id, agency_id, source_type, source_url, price_at_source, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(crypto.randomUUID(), propId, docId, agencyId, doc.source_type, doc.source_url, fields.price ?? null, now).run()

    await env.DB.prepare(
      `UPDATE intake_documents SET status = 'reviewed', property_id = ?, updated_at = ? WHERE id = ?`
    ).bind(propId, now, docId).run()

    const property = await env.DB.prepare('SELECT * FROM properties WHERE id = ?').bind(propId).first()
    return json({ ...parseJsonCols('properties', property), deduped: !isNewProperty }, isNewProperty ? 201 : 200, headers)
  }

  return json({ error: 'Not found' }, 404, headers)
}

async function handleBrochure(req: Request, env: Env, id: string, origin: string | null, url: URL): Promise<Response> {
  const headers = corsHeaders(origin, env)
  const user = await getSessionUser(req, env)
  if (!user) return json({ error: 'Unauthorized' }, 401, headers)
  if (req.method !== 'POST' && req.method !== 'GET') return json({ error: 'Method not allowed' }, 405, headers)

  const row = await env.DB.prepare('SELECT * FROM properties WHERE id = ?').bind(id).first<any>()
  if (!row) return json({ error: 'Not found' }, 404, headers)
  const property = parseJsonCols('properties', row)

  const langParam = (url.searchParams.get('lang') || 'ru').toLowerCase()
  const lang: Lang = langParam === 'en' ? 'en' : 'ru'

  const html = renderBrochureHtml(property, lang)

  let browser
  try {
    browser = await puppeteer.launch(env.BROWSER)
    const page = await browser.newPage()
    // Chromium's print-to-PDF pipeline (page.pdf()) re-rasterizes every
    // photo losslessly regardless of viewport/deviceScaleFactor — a known
    // limitation, not something print options control — which produced a
    // >20MB PDF from ~6 source photos (the reference brochure is 1.7MB).
    // Instead: render at a real print resolution (2x A4/96dpi ≈ print
    // quality), screenshot each `.page` section individually as a
    // compressed JPEG (Puppeteer's screenshot encoder, unlike page.pdf(),
    // honors JPEG quality), then assemble those JPEGs into a PDF with
    // pdf-lib — full control over the actual output size.
    const scale = 2
    await page.setViewport({ width: 794 * scale, height: 1123 * scale, deviceScaleFactor: 1 })
    await page.setContent(html, { waitUntil: 'networkidle0', timeout: 30000 })

    const sectionHandles = await page.$$('.page')
    if (sectionHandles.length === 0) throw new Error('Brochure template produced no .page sections')

    const pdfDoc = await PDFDocument.create()
    const A4_WIDTH_PT = 595.28
    const A4_HEIGHT_PT = 841.89

    for (const handle of sectionHandles) {
      const jpegBytes = await handle.screenshot({ type: 'jpeg', quality: 82 }) as Buffer
      const jpegImage = await pdfDoc.embedJpg(jpegBytes)
      const pdfPage = pdfDoc.addPage([A4_WIDTH_PT, A4_HEIGHT_PT])
      pdfPage.drawImage(jpegImage, { x: 0, y: 0, width: A4_WIDTH_PT, height: A4_HEIGHT_PT })
    }

    const pdfBuffer = Buffer.from(await pdfDoc.save())
    await browser.close()

    const filename = brochureFilename(property, lang)
    return new Response(pdfBuffer, {
      status: 200,
      headers: {
        ...headers,
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="${filename}"`,
      },
    })
  } catch (err: any) {
    if (browser) { try { await browser.close() } catch { /* ignore */ } }
    return json({ error: 'Brochure generation failed', detail: String(err?.message || err) }, 500, headers)
  }
}

// Deliberate: any authenticated agent can read/write any other agent's
// clients/properties/tasks/documents/etc — Mark chose team-wide shared
// visibility for now (2026-09-17), with per-agent scoping planned as a
// follow-up. If you're looking at this because something feels like a
// data-isolation bug, it isn't one yet — check with Mark before adding
// ownership checks here, since flipping this changes what every existing
// team member can see.
// ── local scraper pipeline sync ──────────────────────────────────────
// Authenticated via a shared Bearer token (SYNC_API_TOKEN), not a browser
// session — this is machine-to-machine traffic from the local Camoufox/
// dedup/inpainting pipeline, never a logged-in agent.
//
// Core rule, matching local-pipeline/dedup/cluster_logic.py: nothing is
// ever rejected as a "duplicate". Every scraped listing always gets a
// property_sources row. Coordinate+area clustering only decides which
// `properties` parent it attaches to.

function requireSyncAuth(req: Request, env: Env): boolean {
  const auth = req.headers.get('Authorization') || ''
  const token = auth.replace(/^Bearer\s+/i, '')
  return !!env.SYNC_API_TOKEN && token === env.SYNC_API_TOKEN
}

const COORD_PRECISION = 5
function round5(n: number): number {
  return Math.round(n * 1e5) / 1e5
}

interface SyncChildPayload {
  agency_name: string
  original_listing_url: string
  price: number
  currency?: string
  listing_title?: string
  listing_description?: string
  agent_name?: string
  agent_phone?: string
  agent_email?: string
  source_type?: 'pdf' | 'email' | 'whatsapp' | 'url' | 'manual'
}

interface SyncParentPayload {
  title: string
  description?: string
  address: string
  city?: string
  state?: string
  zip_code?: string
  latitude: number
  longitude: number
  living_area_sqm: number
  district?: string
  property_type?: string
  bedrooms?: number
  bathrooms?: number
  created_by: string // agent id to attribute the property to
}

async function getOrCreateAgency(env: Env, name: string, now: string): Promise<string> {
  const existing = await env.DB.prepare('SELECT id FROM agencies WHERE name = ?').bind(name).first<{ id: string }>()
  if (existing) return existing.id
  const id = crypto.randomUUID()
  await env.DB.prepare(
    `INSERT INTO agencies (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)`
  ).bind(id, name, now, now).run()
  return id
}

async function handleSync(req: Request, env: Env, url: URL, path: string, origin: string | null): Promise<Response> {
  const headers = corsHeaders(origin, env)
  if (!requireSyncAuth(req, env)) return json({ error: 'Unauthorized' }, 401, headers)

  // POST /sync/properties — ingest one scraped listing (parent + one child).
  // Called once per listing found on any of the ~30-40 portals; called many
  // times for the same physical property when different agencies list it.
  if (path === '/sync/properties' && req.method === 'POST') {
    const body = await req.json<{ parent: SyncParentPayload; child: SyncChildPayload }>()
    const { parent, child } = body
    if (!parent || !child) return json({ error: 'Both parent and child are required' }, 400, headers)
    if (!parent.living_area_sqm || parent.living_area_sqm <= 0) {
      return json({ error: 'parent.living_area_sqm must be a positive number' }, 400, headers)
    }
    if (!child.original_listing_url || !child.agency_name || child.price == null) {
      return json({ error: 'child.original_listing_url, agency_name and price are required' }, 400, headers)
    }

    const now = new Date().toISOString()
    const lat5 = round5(parent.latitude)
    const lng5 = round5(parent.longitude)

    // Find-or-create the parent property by spatial cluster. NEVER unique
    // in the schema (see migration 0005) — enforced here, in application
    // logic, so a race or retry updates the existing row instead of
    // erroring and dropping the incoming listing.
    let propertyId: string
    let isNewProperty = true
    const clusterMatch = await env.DB.prepare(
      `SELECT id FROM properties WHERE map_lat = ? AND map_lng = ? AND living_area_sqm = ? LIMIT 1`
    ).bind(lat5, lng5, parent.living_area_sqm).first<{ id: string }>()

    if (clusterMatch) {
      propertyId = clusterMatch.id
      isNewProperty = false
    } else {
      propertyId = crypto.randomUUID()
      const lastProp = await env.DB.prepare(
        `SELECT property_id FROM properties ORDER BY property_id DESC LIMIT 1`
      ).first<{ property_id: string }>()
      const lastNum = lastProp?.property_id?.match(/PROP-(\d+)/)
      const nextPropertyId = `PROP-${((lastNum ? parseInt(lastNum[1]) : 0) + 1).toString().padStart(3, '0')}`

      await env.DB.prepare(
        `INSERT INTO properties (
           id, property_id, address, city, state, zip_code, description,
           property_type, bedrooms, bathrooms, listing_status, pipeline_status,
           map_lat, map_lng, living_area_sqm, created_by, created_at, updated_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 'uncontacted', ?, ?, ?, ?, ?, ?)`
      ).bind(
        propertyId, nextPropertyId, parent.address, parent.city || 'Monaco',
        parent.state || parent.district || '', parent.zip_code || '', parent.description || null,
        parent.property_type || null, parent.bedrooms ?? null, parent.bathrooms ?? null,
        lat5, lng5, parent.living_area_sqm, parent.created_by, now, now,
      ).run()
    }

    const agencyId = await getOrCreateAgency(env, child.agency_name, now)

    // Upsert the child by original_listing_url (UNIQUE in migration 0005).
    // Seeing the same URL again means this agency's listing was re-scraped
    // (price/description refresh) — update in place. A different URL, even
    // against the same property cluster, is always a new competing agency
    // and always gets appended as its own row.
    const existingSource = await env.DB.prepare(
      `SELECT id FROM property_sources WHERE source_url = ?`
    ).bind(child.original_listing_url).first<{ id: string }>()

    let sourceId: string
    let isNewSource = true
    if (existingSource) {
      sourceId = existingSource.id
      isNewSource = false
      await env.DB.prepare(
        `UPDATE property_sources SET
           agency_id = ?, listing_title = ?, listing_description = ?, price_at_source = ?,
           currency = ?, agent_name = ?, agent_phone = ?, agent_email = ?,
           consecutive_404_count = 0, is_off_market = 0, last_checked_at = ?
         WHERE id = ?`
      ).bind(
        agencyId, child.listing_title || null, child.listing_description || null, child.price,
        child.currency || 'EUR', child.agent_name || null, child.agent_phone || null, child.agent_email || null,
        now, sourceId,
      ).run()
    } else {
      sourceId = crypto.randomUUID()
      await env.DB.prepare(
        `INSERT INTO property_sources (
           id, property_id, agency_id, source_type, source_url, price_at_source,
           currency, listing_title, listing_description, agent_name, agent_phone, agent_email,
           status, last_checked_at, created_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)`
      ).bind(
        sourceId, propertyId, agencyId, child.source_type || 'url', child.original_listing_url, child.price,
        child.currency || 'EUR', child.listing_title || null, child.listing_description || null,
        child.agent_name || null, child.agent_phone || null, child.agent_email || null,
        now, now,
      ).run()
    }

    const property = await env.DB.prepare('SELECT * FROM properties WHERE id = ?').bind(propertyId).first()
    return json(
      { property: parseJsonCols('properties', property), source_id: sourceId, is_new_property: isNewProperty, is_new_source: isNewSource },
      isNewProperty || isNewSource ? 201 : 200,
      headers,
    )
  }

  // GET /sync/properties/urls — every live (non-archived) listing URL, for
  // the morning 404 sweep to check.
  if (path === '/sync/properties/urls' && req.method === 'GET') {
    const { results } = await env.DB.prepare(
      `SELECT id, source_url, consecutive_404_count FROM property_sources WHERE is_off_market = 0 AND source_url IS NOT NULL`
    ).all()
    return json(results || [], 200, headers)
  }

  // POST /sync/sources/:id/checkin — the daily 404 sweep reports the result
  // of checking one listing URL. 3 consecutive 404s -> auto off-market.
  const checkinMatch = path.match(/^\/sync\/sources\/([^/]+)\/checkin$/)
  if (checkinMatch && req.method === 'POST') {
    const sourceId = checkinMatch[1]
    const body = await req.json<{ found: boolean }>()
    const now = new Date().toISOString()
    const source = await env.DB.prepare('SELECT * FROM property_sources WHERE id = ?').bind(sourceId).first<any>()
    if (!source) return json({ error: 'Not found' }, 404, headers)

    if (body.found) {
      await env.DB.prepare(
        `UPDATE property_sources SET consecutive_404_count = 0, last_checked_at = ? WHERE id = ?`
      ).bind(now, sourceId).run()
      return json({ id: sourceId, consecutive_404_count: 0, is_off_market: false }, 200, headers)
    }

    const newCount = (source.consecutive_404_count || 0) + 1
    const nowOffMarket = newCount >= 3
    await env.DB.prepare(
      `UPDATE property_sources SET consecutive_404_count = ?, is_off_market = ?, last_checked_at = ? WHERE id = ?`
    ).bind(newCount, nowOffMarket ? 1 : 0, now, sourceId).run()

    // If every source for this property is now off-market, archive the
    // parent too — but only if nothing else is still live.
    if (nowOffMarket) {
      const stillActive = await env.DB.prepare(
        `SELECT COUNT(*) AS n FROM property_sources WHERE property_id = ? AND is_off_market = 0`
      ).bind(source.property_id).first<{ n: number }>()
      if (!stillActive || stillActive.n === 0) {
        await env.DB.prepare(
          `UPDATE properties SET pipeline_status = 'off_market', listing_status = 'withdrawn', updated_at = ? WHERE id = ?`
        ).bind(now, source.property_id).run()
      }
    }

    return json({ id: sourceId, consecutive_404_count: newCount, is_off_market: nowOffMarket }, 200, headers)
  }

  // POST /sync/sources/:id/agree — the team confirmed the true mandate
  // holder. Locks the parent's public display to this source; every
  // sibling source under the same property silently flips to 'losing'
  // (kept forever in the private log, never deleted).
  const agreeMatch = path.match(/^\/sync\/sources\/([^/]+)\/agree$/)
  if (agreeMatch && req.method === 'POST') {
    const sourceId = agreeMatch[1]
    const source = await env.DB.prepare('SELECT * FROM property_sources WHERE id = ?').bind(sourceId).first<any>()
    if (!source) return json({ error: 'Not found' }, 404, headers)
    const now = new Date().toISOString()

    await env.DB.prepare(
      `UPDATE property_sources SET status = CASE WHEN id = ? THEN 'agreed' ELSE 'losing' END WHERE property_id = ?`
    ).bind(sourceId, source.property_id).run()
    await env.DB.prepare(
      `UPDATE properties SET pipeline_status = 'agreed', mandate_source_id = ?, updated_at = ? WHERE id = ?`
    ).bind(sourceId, now, source.property_id).run()

    const property = await env.DB.prepare('SELECT * FROM properties WHERE id = ?').bind(source.property_id).first()
    return json(parseJsonCols('properties', property), 200, headers)
  }

  return json({ error: 'Not found' }, 404, headers)
}

async function handleRest(req: Request, env: Env, url: URL, table: TableName, id: string | null, origin: string | null): Promise<Response> {
  const headers = corsHeaders(origin, env)
  const user = await getSessionUser(req, env)
  if (!user) return json({ error: 'Unauthorized' }, 401, headers)

  if (req.method === 'GET' && !id) {
    let sql = `SELECT * FROM ${table}`
    const conditions: string[] = []
    const params: any[] = []
    const OPS: Record<string, string> = { gte: '>=', lte: '<=', gt: '>', lt: '<', neq: '!=' }
    for (const [key, value] of url.searchParams.entries()) {
      if (key === 'limit' || key === 'offset' || key === 'order') continue
      const opMatch = key.match(/^(.+)__(gte|lte|gt|lt|neq|in|ilike)$/)
      if (opMatch) {
        const [, col, op] = opMatch
        if (op === 'in') {
          const values = value.split(',')
          conditions.push(`${col} IN (${values.map(() => '?').join(',')})`)
          params.push(...values)
        } else if (op === 'ilike') {
          conditions.push(`${col} LIKE ? COLLATE NOCASE`)
          params.push(value.replace(/\*/g, '%'))
        } else {
          conditions.push(`${col} ${OPS[op]} ?`)
          params.push(value)
        }
        continue
      }
      conditions.push(`${key} = ?`)
      params.push(value === 'true' ? 1 : value === 'false' ? 0 : value)
    }
    if (conditions.length) sql += ' WHERE ' + conditions.join(' AND ')
    const order = url.searchParams.get('order')
    if (order) sql += ` ORDER BY ${order}`
    const limit = url.searchParams.get('limit')
    if (limit) sql += ` LIMIT ${parseInt(limit, 10)}`
    const offset = url.searchParams.get('offset')
    if (offset) sql += ` OFFSET ${parseInt(offset, 10)}`

    const { results } = await env.DB.prepare(sql).bind(...params).all()
    return json((results || []).map(r => parseJsonCols(table, r)), 200, headers)
  }

  if (req.method === 'GET' && id) {
    const row = await env.DB.prepare(`SELECT * FROM ${table} WHERE id = ?`).bind(id).first()
    if (!row) return json({ error: 'Not found' }, 404, headers)
    return json(parseJsonCols(table, row), 200, headers)
  }

  if (req.method === 'POST') {
    const body = stringifyJsonCols(table, await req.json<any>())
    if (!body.id) body.id = crypto.randomUUID()
    const now = new Date().toISOString()
    // columnsOf() returns a Set<string> — `in` checks object properties, not
    // Set membership, so this always evaluated false and created_at/updated_at
    // were never auto-filled for any table unless the client happened to set
    // them itself (e.g. activity_logs rows ended up with created_at=NULL,
    // which the dashboard's "time ago" formatting then rendered as
    // 01/01/1970 via `new Date(null)`). Use .has() to actually check.
    const tableCols = await columnsOf(env, table)
    if (tableCols.has('created_at') && !body.created_at) body.created_at = now
    if (tableCols.has('updated_at') && !body.updated_at) body.updated_at = now
    const cols = Object.keys(body)
    const sql = `INSERT INTO ${table} (${cols.join(',')}) VALUES (${cols.map(() => '?').join(',')})`
    await env.DB.prepare(sql).bind(...cols.map(c => body[c])).run()
    const row = await env.DB.prepare(`SELECT * FROM ${table} WHERE id = ?`).bind(body.id).first()
    return json(parseJsonCols(table, row), 201, headers)
  }

  if ((req.method === 'PATCH' || req.method === 'PUT') && id) {
    const body = stringifyJsonCols(table, await req.json<any>())
    delete body.id
    const cols = Object.keys(body)
    if (cols.length === 0) return json({ error: 'Empty update' }, 400, headers)
    const sql = `UPDATE ${table} SET ${cols.map(c => `${c} = ?`).join(',')} WHERE id = ?`
    await env.DB.prepare(sql).bind(...cols.map(c => body[c]), id).run()
    const row = await env.DB.prepare(`SELECT * FROM ${table} WHERE id = ?`).bind(id).first()
    if (!row) return json({ error: 'Not found' }, 404, headers)
    return json(parseJsonCols(table, row), 200, headers)
  }

  if (req.method === 'DELETE' && id) {
    await env.DB.prepare(`DELETE FROM ${table} WHERE id = ?`).bind(id).run()
    return json({ ok: true }, 200, headers)
  }

  return json({ error: 'Method not allowed' }, 405, headers)
}

const columnCache = new Map<string, Set<string>>()
async function columnsOf(env: Env, table: string): Promise<Set<string>> {
  if (columnCache.has(table)) return columnCache.get(table)!
  const { results } = await env.DB.prepare(`PRAGMA table_info(${table})`).all()
  const set = new Set((results || []).map((r: any) => r.name))
  columnCache.set(table, set)
  return set
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url)
    const origin = req.headers.get('Origin')

    if (req.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin, env) })
    }

    if (url.pathname.startsWith('/auth/')) {
      return handleAuth(req, env, url.pathname, origin)
    }

    if (url.pathname.startsWith('/public/')) {
      return handlePublic(req, env, url.pathname, origin)
    }

    if (url.pathname.startsWith('/documents/upload') || url.pathname.startsWith('/documents/file/') || url.pathname === '/photos/upload') {
      return handleDocumentFiles(req, env, url, url.pathname, origin)
    }

    if (url.pathname.startsWith('/intake/')) {
      return handleIntake(req, env, url, url.pathname, origin)
    }

    if (url.pathname.startsWith('/sync/')) {
      return handleSync(req, env, url, url.pathname, origin)
    }

    const parts = url.pathname.replace(/^\//, '').split('/')
    const table = parts[0] as TableName
    const id = parts[1] || null

    if (table === 'properties' && id && parts[2] === 'brochure') {
      return handleBrochure(req, env, id, origin, url)
    }

    if (TABLES.includes(table)) {
      return handleRest(req, env, url, table, id, origin)
    }

    return json({ error: 'Not found' }, 404, corsHeaders(origin, env))
  },
}

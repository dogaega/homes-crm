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

export interface Env {
  DB: D1Database
  ALLOWED_ORIGINS: string
}

const TABLES = [
  'agents', 'clients', 'properties', 'tasks', 'task_comments',
  'task_templates', 'documents', 'document_templates', 'document_signatures',
  'communications', 'showings', 'inquiries', 'client_property_interests',
  'activity_logs',
] as const
type TableName = typeof TABLES[number]

const JSON_COLUMNS: Record<string, string[]> = {
  agents: ['performance_metrics', 'social_media', 'territory', 'specialties'],
  clients: ['budget_range', 'preferences', 'tags'],
  properties: ['features', 'photos'],
  tasks: [],
  task_templates: ['tasks'],
  documents: ['field_values', 'tags'],
  document_templates: ['template_fields'],
  inquiries: ['preferred_locations'],
  activity_logs: ['metadata'],
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
    const { email, password, full_name } = await req.json<any>()
    if (!email || !password) return json({ error: 'email and password required' }, 400, headers)
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
      params.push(value)
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
    if ('created_at' in (await columnsOf(env, table)) && !body.created_at) body.created_at = now
    if ('updated_at' in (await columnsOf(env, table)) && !body.updated_at) body.updated_at = now
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

    const parts = url.pathname.replace(/^\//, '').split('/')
    const table = parts[0] as TableName
    const id = parts[1] || null
    if (TABLES.includes(table)) {
      return handleRest(req, env, url, table, id, origin)
    }

    return json({ error: 'Not found' }, 404, corsHeaders(origin, env))
  },
}

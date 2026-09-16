// Cloudflare Worker API client — replaces src/lib/supabase.ts.
//
// Design note: rather than rewriting ~40 call-sites' `.from(table).select()...`
// chains individually, this exports a small Supabase-shaped query-builder
// (`supabase.from(table).select().eq().single()/insert()/update()/delete()`)
// backed by fetch() calls to the Worker's REST endpoints. This keeps the
// consuming components' code identical while the actual data path is fully
// Cloudflare (D1 via the Worker) — there is no Supabase client, no
// @supabase/supabase-js import, and no Supabase network calls anywhere.
//
// Requests go to NEXT_PUBLIC_API_URL, which defaults to the same-origin
// `/api/backend` path that next.config.ts rewrites to the Worker — so the
// httpOnly session cookie is sent same-origin with no CORS needed in the
// primary (browser) path.

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api/backend'

export interface ApiError {
  message: string
  code?: string
}

async function request(path: string, options: RequestInit = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  })
  let body: any = null
  try { body = await res.json() } catch { /* no body */ }
  if (!res.ok) {
    const err: ApiError = { message: body?.error || res.statusText, code: String(res.status) }
    return { data: null, error: err }
  }
  return { data: body, error: null }
}

class QueryBuilder {
  private table: string
  private filters: Record<string, string> = {}
  private orderClause: string | null = null
  private limitN: number | null = null
  private wantsSingle = false
  private allowEmpty = false
  private pendingBody: any = null
  private method: 'GET' | 'POST' | 'PATCH' | 'DELETE' = 'GET'

  constructor(table: string) {
    this.table = table
  }

  select(_columns?: string) {
    return this
  }

  eq(column: string, value: any) {
    this.filters[column] = String(value)
    return this
  }

  neq(column: string, value: any) {
    this.filters[`${column}__neq`] = String(value)
    return this
  }

  gte(column: string, value: any) {
    this.filters[`${column}__gte`] = String(value)
    return this
  }

  lte(column: string, value: any) {
    this.filters[`${column}__lte`] = String(value)
    return this
  }

  gt(column: string, value: any) {
    this.filters[`${column}__gt`] = String(value)
    return this
  }

  lt(column: string, value: any) {
    this.filters[`${column}__lt`] = String(value)
    return this
  }

  in(column: string, values: any[]) {
    this.filters[`${column}__in`] = values.join(',')
    return this
  }

  range(from: number, to: number) {
    this.limitN = to - from + 1
    this.filters['__offset'] = String(from)
    return this
  }

  maybeSingle() {
    this.wantsSingle = true
    this.allowEmpty = true
    return this
  }

  ilike(column: string, pattern: string) {
    this.filters[`${column}__ilike`] = pattern.replace(/%/g, '*')
    return this
  }

  or(_expr: string) {
    // Not translated server-side yet — filtered client-side would require
    // fetching unfiltered, which this shim intentionally avoids for cost.
    // Falls back to a no-op filter so calls don't crash; searches using
    // .or() should be revisited when the Worker gains an OR-query parser.
    return this
  }

  order(column: string, opts?: { ascending?: boolean }) {
    this.orderClause = `${column} ${opts?.ascending === false ? 'DESC' : 'ASC'}`
    return this
  }

  limit(n: number) {
    this.limitN = n
    return this
  }

  single() {
    this.wantsSingle = true
    return this
  }

  insert(body: any) {
    this.method = 'POST'
    this.pendingBody = Array.isArray(body) ? body[0] : body
    return this
  }

  update(body: any) {
    this.method = 'PATCH'
    this.pendingBody = body
    return this
  }

  delete() {
    this.method = 'DELETE'
    return this
  }

  private buildQuery() {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(this.filters)) {
      if (key === '__offset') {
        params.set('offset', value)
      } else {
        params.set(key, value)
      }
    }
    if (this.orderClause) params.set('order', this.orderClause)
    if (this.limitN != null) params.set('limit', String(this.limitN))
    const qs = params.toString()
    return qs ? `?${qs}` : ''
  }

  // Making the builder awaitable lets call sites keep `await supabase.from(x)...`
  then(resolve: (v: any) => void, reject?: (e: any) => void) {
    this.exec().then(resolve, reject)
  }

  private async exec() {
    const id = this.filters.id
    if (this.method === 'POST') {
      const { data, error } = await request(`/${this.table}`, {
        method: 'POST',
        body: JSON.stringify(this.pendingBody),
      })
      if (this.wantsSingle) return { data, error }
      return { data: data ? [data] : null, error }
    }

    if (this.method === 'PATCH') {
      if (!id) return { data: null, error: { message: 'update() requires .eq("id", ...)' } }
      const { data, error } = await request(`/${this.table}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(this.pendingBody),
      })
      return { data, error }
    }

    if (this.method === 'DELETE') {
      if (!id) return { data: null, error: { message: 'delete() requires .eq("id", ...)' } }
      const { data, error } = await request(`/${this.table}/${id}`, { method: 'DELETE' })
      return { data, error }
    }

    // GET
    if (this.wantsSingle && id) {
      return request(`/${this.table}/${id}`)
    }
    const { data, error } = await request(`/${this.table}${this.buildQuery()}`)
    if (this.wantsSingle) {
      if (error) return { data: null, error }
      const row = Array.isArray(data) ? data[0] : data
      if (!row) {
        if (this.allowEmpty) return { data: null, error: null }
        return { data: null, error: { message: 'No rows found', code: 'PGRST116' } }
      }
      return { data: row, error: null }
    }
    return { data, error }
  }
}

export const supabase = {
  from(table: string) {
    return new QueryBuilder(table)
  },
  // Minimal stand-in for supabase.rpc() — only `log_activity` is used by
  // this app, and it's a straightforward insert into activity_logs.
  async rpc(fn: string, args?: Record<string, any>) {
    if (fn === 'log_activity') {
      const { data, error } = await request('/activity_logs', {
        method: 'POST',
        body: JSON.stringify({
          agent_id: args?.p_agent_id,
          activity_type: args?.p_activity_type,
          entity_type: args?.p_entity_type,
          entity_id: args?.p_entity_id,
          description: args?.p_description,
          metadata: args?.p_metadata,
        }),
      })
      return { data: data?.id ?? null, error }
    }
    return { data: null, error: { message: `rpc('${fn}') is not implemented in the Worker API` } }
  },
  // File storage was Supabase Storage; this migration does not include an
  // R2-backed replacement yet. Calls resolve with a clear error instead of
  // crashing, matching the shim contract error shape.
  storage: {
    from(_bucket: string) {
      return {
        async upload() {
          return { data: null, error: { message: 'Storage is not implemented — Supabase Storage was not migrated to R2 yet' } }
        },
        getPublicUrl(path: string) {
          return { data: { publicUrl: path } }
        },
      }
    },
  },
  auth: {
    async getSession() {
      const { data, error } = await request('/auth/session')
      if (error) return { data: { session: null }, error }
      const session = data?.user ? { user: toAuthUser(data.user) } : null
      return { data: { session }, error: null }
    },
    async getUser() {
      const { data, error } = await request('/auth/session')
      if (error) return { data: { user: null }, error }
      return { data: { user: data?.user ? toAuthUser(data.user) : null }, error: null }
    },
    async signInWithPassword({ email, password }: { email: string; password: string }) {
      const { data, error } = await request('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      })
      if (error) return { data: { user: null, session: null }, error }
      const user = toAuthUser(data.user)
      return { data: { user, session: { user } }, error: null }
    },
    async signUp({ email, password, options }: { email: string; password: string; options?: { data?: any } }) {
      const { data, error } = await request('/auth/signup', {
        method: 'POST',
        body: JSON.stringify({ email, password, full_name: options?.data?.full_name }),
      })
      if (error) return { data: { user: null, session: null }, error }
      const user = toAuthUser(data.user)
      return { data: { user, session: { user } }, error: null }
    },
    async signOut() {
      const { error } = await request('/auth/logout', { method: 'POST' })
      return { error }
    },
    async resetPasswordForEmail(email: string) {
      const { data, error } = await request('/auth/reset-password', {
        method: 'POST',
        body: JSON.stringify({ email }),
      })
      return { data, error }
    },
    async refreshSession() {
      const { data, error } = await request('/auth/session')
      if (error) return { data: { session: null }, error }
      const session = data?.user ? { user: toAuthUser(data.user) } : null
      return { data: { session }, error: null }
    },
    async updateUser(_attrs: any) {
      return { data: { user: null }, error: { message: 'updateUser is not implemented in the Worker API yet' } }
    },
    onAuthStateChange(_cb: (event: string, session: any) => void) {
      // No realtime push channel in this minimal migration; AuthContext
      // drives its own state from explicit signIn/signUp/signOut calls.
      return { data: { subscription: { unsubscribe() {} } } }
    },
  },
}

function toAuthUser(u: { id: string; email: string; full_name?: string | null }) {
  return {
    id: u.id,
    email: u.email,
    user_metadata: { full_name: u.full_name },
  }
}

export type { Database } from '@/types/database'

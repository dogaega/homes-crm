// Market listings + client requests (Worker /pipeline/* endpoints).
// Same-origin through the /api/backend rewrite, so the session cookie is sent.

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api/backend'

export interface ListingCard {
  id: string
  property_id: string
  property_name: string | null
  price: number | null
  transaction_type: 'sale' | 'rent'
  city: string | null
  quarter: string | null
  building_name: string | null
  bedrooms: number | null
  room_count: number | null
  living_area_sqm: number | null
  terrace_sqm: number | null
  floor: number | null
  sea_view: number | null
  property_type: string | null
  hero_image_key: string | null
  source_count: number
  first_seen_at: string
  last_seen_at: string
  pipeline_status: string | null
  contact_count: number
  photo_url: string | null
  score?: number
  is_new?: boolean
}

export interface RequestCriteria {
  transaction_type: 'sale' | 'rent'
  cities: string[]
  quarters: string[]
  kind: 'apartment' | 'villa' | 'any'
  commercial: boolean
  bedrooms_min: number | null
  bedrooms_max: number | null
  area_min: number | null
  area_max: number | null
  price_max: number | null
  location_text: string
  location_unrecognized?: boolean
  notes: string
  source: 'sheet' | 'manual'
  client_name: string
  broker?: string | null
  deadline?: string | null
}

export interface ClientRequest {
  id: string
  name: string
  criteria: RequestCriteria
  created_at: string
  match_count: number
  new_count: number
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/pipeline${path}`, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`)
  return body as T
}

export const pipelineApi = {
  listings: (params: URLSearchParams) =>
    call<{ total: number; page: number; page_size: number; items: ListingCard[] }>(`/listings?${params}`),
  cities: () => call<{ city: string; n: number }[]>('/cities'),
  map: (params: URLSearchParams) => call<{ total: number; points: any[] }>(`/map?${params}`),
  reviews: () => call<{ total: number; items: any[] }>('/review'),
  decideReview: (id: string, decision: 'merge' | 'reject') =>
    call<unknown>(`/review/${encodeURIComponent(id)}`, { method: 'POST', body: JSON.stringify({ decision }) }),
  listing: (id: string) => call<any>(`/listings/${encodeURIComponent(id)}`),
  markContacted: (id: string, sourceId?: string, note?: string) =>
    call<{ id: string }>(`/listings/${encodeURIComponent(id)}/contact`, {
      method: 'POST', body: JSON.stringify({ source_id: sourceId, note }),
    }),
  requests: (sync = false) =>
    call<{ sync: { synced: boolean; error?: string }; items: ClientRequest[] }>(`/requests${sync ? '?sync=1' : ''}`),
  matches: (id: string) =>
    call<{ id: string; name: string; criteria: RequestCriteria; items: ListingCard[] }>(`/requests/${encodeURIComponent(id)}/matches`),
  addRequest: (body: Record<string, string | boolean>) =>
    call<{ id: string }>('/requests', { method: 'POST', body: JSON.stringify(body) }),
  deleteRequest: (id: string) => call<{ deleted: boolean }>(`/requests/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

export const heroUrl = (key: string | null) =>
  key ? `${API_BASE}/pipeline/hero/${key.split('/').map(encodeURIComponent).join('/')}` : null

export function formatPrice(price: number | null, tx: string, onRequest: string, perMonth: string): string {
  if (price == null) return onRequest
  const s = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(price)
  return tx === 'rent' ? `${s}${perMonth}` : s
}

export const QUARTER_NAMES: Record<string, string> = {
  'monte-carlo': 'Monte-Carlo', 'fontvieille': 'Fontvieille', 'la-rousse': 'La Rousse', 'la-condamine': 'La Condamine',
  'moneghetti': 'Moneghetti', 'monaco-ville': 'Monaco-Ville', 'larvotto': 'Larvotto', 'mareterra': 'Mareterra',
  'la-colle': 'La Colle', 'les-revoires': 'Les Révoires', 'saint-michel': 'Saint-Michel',
}

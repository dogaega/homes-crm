import { NextResponse } from 'next/server'
import type { Database } from '@/types/database'

// Replaces the old Cloudflare Worker `/api/backend/properties` rewrite.
// Runs server-side only — ESPOCRM_API_KEY must never be exposed to the
// browser, so this route (not a client-side fetch, not a next.config.ts
// rewrite) is what's allowed to hold it.
export const dynamic = 'force-dynamic' // listings change frequently; never serve a stale Next.js Data Cache hit

type Property = Database['public']['Tables']['properties']['Row']

// --------------------------------------------------------------------------
// *** CONFIRM AGAINST YOUR ESPOCRM INSTANCE BEFORE RELYING ON THIS IN PROD ***
// Same caveat as local-pipeline/sync/sync_to_espocrm.py: these are the
// expected EspoCRM Real Estate extension field names. Verify each one under
// Administration > Entity Manager > RealEstateProperty > Fields and correct
// any mismatch here before going live.
// --------------------------------------------------------------------------
const ESPOCRM_FIELDS = {
  name: 'name',
  description: 'description',
  price: 'price',
  bedrooms: 'bedroomsNumber',
  bathrooms: 'bathroomsNumber',
  area: 'houseArea',
  addressStreet: 'addressStreet',
  addressCity: 'addressCity',
  addressState: 'addressState',
  addressPostalCode: 'addressPostalCode',
  latitude: 'latitude',
  longitude: 'longitude',
  status: 'status',
  isPublic: 'isPublic', // the "public_listing" gate field
  createdById: 'createdById',
  createdAt: 'createdAt',
  modifiedAt: 'modifiedAt',
  imagesField: 'images', // multi-attachment field; ids come back as `${imagesField}Ids`
} as const

// EspoCRM's status enum values are whatever your Field Manager has
// configured for RealEstateProperty.status — confirm the exact option
// labels there and adjust this map. Anything not listed falls through to
// 'active' rather than crashing the whole feed over one unmapped status.
const STATUS_MAP: Record<string, Property['listing_status']> = {
  Active: 'active',
  'For Sale': 'active',
  Pending: 'pending',
  'Under Offer': 'pending',
  Sold: 'sold',
  Withdrawn: 'withdrawn',
  Expired: 'expired',
}

const ESPOCRM_API_URL = process.env.ESPOCRM_API_URL // e.g. http://espocrm-host:8080/api/v1
const ESPOCRM_API_KEY = process.env.ESPOCRM_API_KEY
const ESPOCRM_SITE_URL = process.env.ESPOCRM_SITE_URL // e.g. http://espocrm-host:8080, used to build attachment download URLs

const PAGE_SIZE = 200
const FETCH_TIMEOUT_MS = 15_000

interface EspoListResponse {
  total: number
  list: Record<string, unknown>[]
}

async function fetchEspoPage(offset: number): Promise<EspoListResponse> {
  const params = new URLSearchParams({
    offset: String(offset),
    maxSize: String(PAGE_SIZE),
    // Server-side filter as the first line of defense — only ever ask
    // EspoCRM for public listings in the first place.
    'where[0][type]': 'equals',
    'where[0][attribute]': ESPOCRM_FIELDS.isPublic,
    'where[0][value]': 'true',
  })

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS)

  try {
    const res = await fetch(`${ESPOCRM_API_URL}/RealEstateProperty?${params.toString()}`, {
      headers: {
        'X-Api-Key': ESPOCRM_API_KEY as string,
        'Content-Type': 'application/json',
      },
      signal: controller.signal,
      cache: 'no-store',
    })

    if (!res.ok) {
      const body = await res.text().catch(() => '')
      throw new Error(`EspoCRM returned ${res.status} ${res.statusText}: ${body.slice(0, 500)}`)
    }

    return (await res.json()) as EspoListResponse
  } finally {
    clearTimeout(timeout)
  }
}

function buildImageUrls(record: Record<string, unknown>): string[] {
  const idsField = `${ESPOCRM_FIELDS.imagesField}Ids`
  const ids = record[idsField]
  if (!Array.isArray(ids)) return []
  return ids
    .filter((id): id is string => typeof id === 'string' && id.length > 0)
    .map((id) => `${ESPOCRM_SITE_URL}/?entryPoint=download&id=${id}`)
}

function toStringField(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

function toNumberField(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && !Number.isNaN(Number(value))) return Number(value)
  return null
}

/**
 * Normalizes one EspoCRM RealEstateProperty record into the exact flat
 * `Property` row shape PropertyCard.tsx and the map components already
 * consume — so nothing downstream of this route needs to change.
 */
function normalizeEspoProperty(record: Record<string, unknown>): Property {
  const id = toStringField(record.id)

  return {
    id,
    property_id: id, // EspoCRM has no equivalent of our "PROP-001" label; reuse the record id
    address: toStringField(record[ESPOCRM_FIELDS.addressStreet]),
    city: toStringField(record[ESPOCRM_FIELDS.addressCity], 'Monaco'),
    state: toStringField(record[ESPOCRM_FIELDS.addressState]),
    zip_code: toStringField(record[ESPOCRM_FIELDS.addressPostalCode], '98000'),
    price: toNumberField(record[ESPOCRM_FIELDS.price]),
    bedrooms: toNumberField(record[ESPOCRM_FIELDS.bedrooms]),
    bathrooms: toNumberField(record[ESPOCRM_FIELDS.bathrooms]),
    square_feet: toNumberField(record[ESPOCRM_FIELDS.area]),
    lot_size: null,
    year_built: null,
    property_type: null,
    listing_status: STATUS_MAP[toStringField(record[ESPOCRM_FIELDS.status])] ?? 'active',
    listing_date: toStringField(record[ESPOCRM_FIELDS.createdAt]) || null,
    sold_date: null,
    assigned_agent_id: null,
    mls_number: null,
    description: toStringField(record[ESPOCRM_FIELDS.description]) || null,
    features: null,
    photos: buildImageUrls(record),
    virtual_tour_url: null,
    created_at: toStringField(record[ESPOCRM_FIELDS.createdAt]) || null,
    updated_at: toStringField(record[ESPOCRM_FIELDS.modifiedAt]) || null,
    created_by: toStringField(record[ESPOCRM_FIELDS.createdById]),
    data_incomplete: toNumberField(record[ESPOCRM_FIELDS.price]) === null || toNumberField(record[ESPOCRM_FIELDS.area]) === null,
    slug: null,
    plot_size: null,
    terrain_description: null,
    floor_count: null,
    house_history: null,
    concept_description: null,
    construction_details: null,
    engineering_details: null,
    room_layout: null,
    floor_plan_urls: null,
    gallery_urls: buildImageUrls(record),
    video_url: null,
    map_lat: toNumberField(record[ESPOCRM_FIELDS.latitude]),
    map_lng: toNumberField(record[ESPOCRM_FIELDS.longitude]),
    featured: 0,
    public_listing: 1, // safe to hardcode: everything reaching this point already passed the isPublic filter below
  }
}

export async function GET() {
  if (!ESPOCRM_API_URL || !ESPOCRM_API_KEY || !ESPOCRM_SITE_URL) {
    console.error('Missing ESPOCRM_API_URL / ESPOCRM_API_KEY / ESPOCRM_SITE_URL environment variables')
    return NextResponse.json({ error: 'Property feed is not configured' }, { status: 500 })
  }

  try {
    const records: Record<string, unknown>[] = []
    let offset = 0

    // Page through every public listing — a single EspoCRM call caps out
    // at PAGE_SIZE, and this fetches all pages rather than silently
    // truncating the public map to the first 200 properties.
    for (;;) {
      const page = await fetchEspoPage(offset)
      records.push(...page.list)
      if (page.list.length < PAGE_SIZE || records.length >= page.total) break
      offset += PAGE_SIZE
    }

    const properties = records
      // Safe fallback, redundant with the server-side `where` filter above
      // on purpose: even if a future query change ever drops that filter,
      // or EspoCRM returns a record the filter didn't catch, a non-public
      // listing is never allowed to reach the response body.
      .filter((record) => record[ESPOCRM_FIELDS.isPublic] === true)
      .map(normalizeEspoProperty)

    return NextResponse.json(properties, {
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch (error) {
    console.error('Failed to fetch properties from EspoCRM:', error)
    return NextResponse.json({ error: 'Failed to load properties' }, { status: 502 })
  }
}

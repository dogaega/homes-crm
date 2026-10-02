'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { List } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import AuthedPage from '@/components/listings/AuthedPage'
import { formatPrice, pipelineApi, QUARTER_NAMES } from '@/lib/pipelineApi'
import type { Bounds, MapPoint } from '@/components/listings/MapView'

const MapView = dynamic(() => import('@/components/listings/MapView'), { ssr: false })

// Same filters as the list (shared in localStorage), plus the visible map area.
export default function ListingsMapPage() {
  const { t } = useLanguage()
  const [filters, setFilters] = useState<Record<string, any>>({})
  const [bounds, setBounds] = useState<Bounds | null>(null)
  const [points, setPoints] = useState<MapPoint[]>([])
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => { try { setFilters(JSON.parse(localStorage.getItem('listings.filters') || '{}')) } catch { /* none */ } }, [])
  const set = (patch: Record<string, string>) => setFilters(f => {
    const next = { ...f, ...patch }
    try { localStorage.setItem('listings.filters', JSON.stringify(next)) } catch { /* private mode */ }
    return next
  })

  const load = useCallback(() => {
    if (!bounds) return
    const p = new URLSearchParams()
    ;(filters.cities || []).forEach((c: string) => p.append('city', c))
    for (const k of ['tx', 'type', 'min', 'max', 'beds', 'area', 'days', 'q']) if (filters[k]) p.set(k, filters[k])
    for (const [k, v] of Object.entries(bounds)) p.set(k, String(v))
    pipelineApi.map(p).then(r => setPoints(r.points)).catch(e => setError(e.message))
  }, [filters, bounds])
  useEffect(() => { clearTimeout(timer.current); timer.current = setTimeout(load, 300); return () => clearTimeout(timer.current) }, [load])

  const labels = useMemo(() => ({ open: t('listings.open'), approx: t('map.approx'), beds: t('listings.beds') }), [t])
  const inView = [...points].sort((a, b) => Number(b.exact) - Number(a.exact)).slice(0, 40)
  const input = 'h-9 rounded-md border border-border bg-background px-2 text-sm text-foreground'

  return (
    <AuthedPage title={t('listings.title')}>
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <Link href="/listings" className="inline-flex items-center gap-1 h-9 px-3 rounded-md border border-border text-sm"><List className="w-4 h-4" />{t('map.list')}</Link>
        <select className={input} value={filters.tx || ''} onChange={e => set({ tx: e.target.value })}>
          <option value="">{t('listings.sale')} + {t('listings.rent')}</option>
          <option value="sale">{t('listings.sale')}</option>
          <option value="rent">{t('listings.rent')}</option>
        </select>
        <select className={input} value={filters.beds || ''} onChange={e => set({ beds: e.target.value })}>
          <option value="">{t('listings.bedrooms')}</option>
          {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}+</option>)}
        </select>
        <input className={`${input} w-24`} inputMode="numeric" placeholder={t('listings.maxPrice')} value={filters.max || ''}
               onChange={e => set({ max: e.target.value.replace(/\D/g, '') })} />
        <span className="text-sm text-muted-foreground ml-auto">{points.length} {t('map.inView')}</span>
      </div>
      <p className="text-xs text-muted-foreground mb-2">{t('map.legend')}</p>
      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 text-destructive text-sm p-2 mb-2">{error}</div>}
      <div className="h-[62vh] mb-3"><MapView points={points} onBounds={setBounds} labels={labels} /></div>
      <div className="divide-y divide-border rounded-lg border border-border">
        {inView.map(p => (
          <Link key={p.id} href={`/listings/${p.id}`} className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-accent">
            <span className={`w-2 h-2 rounded-full shrink-0 ${p.exact ? 'bg-foreground' : 'bg-gray-400'}`} />
            <span className="font-semibold whitespace-nowrap">{formatPrice(p.price, p.tx, t('listings.onRequest'), t('listings.perMonth'))}</span>
            <span className="truncate">{p.title || '—'}</span>
            <span className="ml-auto text-xs text-muted-foreground whitespace-nowrap">
              {[p.beds != null ? `${p.beds} ${t('listings.beds')}` : null, p.area ? `${Math.round(p.area)} m²` : null,
                p.city === 'Monaco' ? (QUARTER_NAMES[p.quarter || ''] || '') : p.city].filter(Boolean).join(' · ')}
            </span>
          </Link>
        ))}
      </div>
    </AuthedPage>
  )
}

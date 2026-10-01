'use client'

import { useCallback, useEffect, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import AuthedPage from '@/components/listings/AuthedPage'
import ListingCardView from '@/components/listings/ListingCardView'
import { pipelineApi, type ListingCard } from '@/lib/pipelineApi'

interface Filters {
  cities: string[]; tx: string; type: string; min: string; max: string; beds: string; area: string; days: string; q: string; sort: string
}
const EMPTY: Filters = { cities: [], tx: '', type: '', min: '', max: '', beds: '', area: '', days: '', q: '', sort: '' }
const FILTER_KEY = 'listings.filters'

export default function ListingsPage() {
  const { t } = useLanguage()
  const [filters, setFilters] = useState<Filters>(() => {
    try { return { ...EMPTY, ...JSON.parse(localStorage.getItem(FILTER_KEY) || '{}') } } catch { return EMPTY }
  })
  const [page, setPage] = useState(0)
  const [cities, setCities] = useState<{ city: string; n: number }[]>([])
  const [data, setData] = useState<{ total: number; page_size: number; items: ListingCard[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => { pipelineApi.cities().then(setCities).catch(() => {}) }, [])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    const p = new URLSearchParams()
    filters.cities.forEach(c => p.append('city', c))
    for (const k of ['tx', 'type', 'min', 'max', 'beds', 'area', 'days', 'q', 'sort'] as const) if (filters[k]) p.set(k, filters[k])
    p.set('page', String(page))
    try { setData(await pipelineApi.listings(p)) } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }, [filters, page])

  useEffect(() => {
    const id = setTimeout(load, 250)
    try { localStorage.setItem(FILTER_KEY, JSON.stringify(filters)) } catch { /* private mode */ }
    return () => clearTimeout(id)
  }, [load, filters])

  const set = (patch: Partial<Filters>) => { setFilters(f => ({ ...f, ...patch })); setPage(0) }
  const toggleCity = (c: string) => set({ cities: filters.cities.includes(c) ? filters.cities.filter(x => x !== c) : [...filters.cities, c] })
  const pages = data ? Math.ceil(data.total / data.page_size) : 0
  const input = 'h-9 rounded-md border border-border bg-background px-2 text-sm text-foreground'

  return (
    <AuthedPage title={t('listings.title')}>
      <p className="text-sm text-muted-foreground mb-4">{t('listings.subtitle')}</p>

      <div className="flex flex-wrap gap-1.5 mb-3">
        <button onClick={() => set({ cities: [] })}
          className={`px-3 py-1 rounded-full text-sm border ${filters.cities.length === 0 ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-foreground'}`}>
          {t('listings.allTowns')}
        </button>
        {cities.map(c => (
          <button key={c.city} onClick={() => toggleCity(c.city)}
            className={`px-3 py-1 rounded-full text-sm border ${filters.cities.includes(c.city) ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-foreground'}`}>
            {c.city} <span className="opacity-60">{c.n}</span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 items-center mb-4">
        <select className={input} value={filters.tx} onChange={e => set({ tx: e.target.value })}>
          <option value="">{t('listings.sale')} + {t('listings.rent')}</option>
          <option value="sale">{t('listings.sale')}</option>
          <option value="rent">{t('listings.rent')}</option>
        </select>
        <select className={input} value={filters.type} onChange={e => set({ type: e.target.value })}>
          <option value="">{t('listings.any')}</option>
          <option value="apartment">{t('listings.apartment')}</option>
          <option value="villa">{t('listings.villa')}</option>
        </select>
        <input className={`${input} w-28`} inputMode="numeric" placeholder={t('listings.minPrice')} value={filters.min} onChange={e => set({ min: e.target.value.replace(/\D/g, '') })} />
        <input className={`${input} w-28`} inputMode="numeric" placeholder={t('listings.maxPrice')} value={filters.max} onChange={e => set({ max: e.target.value.replace(/\D/g, '') })} />
        <select className={input} value={filters.beds} onChange={e => set({ beds: e.target.value })}>
          <option value="">{t('listings.bedrooms')}</option>
          {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}+</option>)}
        </select>
        <input className={`${input} w-20`} inputMode="numeric" placeholder={t('listings.minArea')} value={filters.area} onChange={e => set({ area: e.target.value.replace(/\D/g, '') })} />
        <select className={input} value={filters.days} onChange={e => set({ days: e.target.value })}>
          <option value="">{t('listings.newOnly')}: —</option>
          <option value="1">{t('listings.newOnly')}: {t('listings.days1')}</option>
          <option value="7">{t('listings.newOnly')}: {t('listings.days7')}</option>
        </select>
        <input className={`${input} w-56`} placeholder={t('listings.search')} value={filters.q} onChange={e => set({ q: e.target.value })} />
        <select className={input} value={filters.sort} onChange={e => set({ sort: e.target.value })}>
          <option value="">{t('listings.sortNew')}</option>
          <option value="price_asc">{t('listings.sortPriceAsc')}</option>
          <option value="price_desc">{t('listings.sortPriceDesc')}</option>
          <option value="area">{t('listings.sortArea')}</option>
        </select>
        {data && <span className="text-sm text-muted-foreground ml-auto">{data.total.toLocaleString('fr-FR')} {t('listings.results')}</span>}
      </div>

      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 text-destructive text-sm p-3 mb-4">{error}</div>}
      {loading && !data && <p className="text-muted-foreground">{t('listings.loading')}</p>}
      {data && data.items.length === 0 && !loading && <p className="text-muted-foreground">{t('listings.none')}</p>}

      <div className={`grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 ${loading ? 'opacity-60' : ''}`}>
        {data?.items.map(p => <ListingCardView key={p.id} p={p} />)}
      </div>

      {pages > 1 && (
        <div className="flex justify-center items-center gap-3 mt-6">
          <button disabled={page === 0} onClick={() => setPage(p => p - 1)} className="px-3 py-1.5 rounded-md border border-border text-sm disabled:opacity-40">{t('listings.prev')}</button>
          <span className="text-sm text-muted-foreground">{page + 1} / {pages}</span>
          <button disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)} className="px-3 py-1.5 rounded-md border border-border text-sm disabled:opacity-40">{t('listings.next')}</button>
        </div>
      )}
    </AuthedPage>
  )
}

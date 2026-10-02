'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Plus, Trash2, ArrowLeft } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import AuthedPage from '@/components/listings/AuthedPage'
import ListingCardView from '@/components/listings/ListingCardView'
import { formatPrice, pipelineApi, QUARTER_NAMES, type ClientRequest, type ListingCard } from '@/lib/pipelineApi'

const EMPTY_FORM = { client_name: '', type: 'Квартира', location: '', bedrooms: '', area: '', price: '', rent: '', notes: '' }

export default function RequestsPage() {
  const { t } = useLanguage()
  const [items, setItems] = useState<ClientRequest[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [matches, setMatches] = useState<ListingCard[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [form, setForm] = useState<typeof EMPTY_FORM | null>(null)

  const load = useCallback(async (sync = false) => {
    setSyncing(sync); setError(null)
    try {
      const r = await pipelineApi.requests(sync)
      if (r.sync.error) setError(r.sync.error)
      setItems(r.items)
    } catch (e) { setError((e as Error).message) } finally { setSyncing(false) }
  }, [])
  useEffect(() => { load() }, [load])

  useEffect(() => {
    window.scrollTo({ top: 0 })
    if (!selected) return
    setMatches(null)
    pipelineApi.matches(selected).then(r => setMatches(r.items)).catch(e => setError(e.message))
  }, [selected])

  const save = async () => {
    if (!form) return
    try {
      const { id } = await pipelineApi.addRequest(form)
      setForm(null); await load(); setSelected(id)
    } catch (e) { setError((e as Error).message) }
  }
  const remove = async (id: string) => {
    try { await pipelineApi.deleteRequest(id); if (selected === id) setSelected(null); await load() } catch (e) { setError((e as Error).message) }
  }

  const sel = items?.find(i => i.id === selected)
  const input = 'h-9 w-full rounded-md border border-border bg-background px-2 text-sm text-foreground'
  const summary = (r: ClientRequest) => {
    const c = r.criteria
    const where = c.cities.length ? c.cities.join(', ') : t('requests.anyTown')
    const q = c.quarters.map(x => QUARTER_NAMES[x] || x).join(', ')
    const beds = c.bedrooms_min != null ? `${c.bedrooms_min}${c.bedrooms_max && c.bedrooms_max !== c.bedrooms_min ? `–${c.bedrooms_max}` : ''} ${t('listings.beds')}` : ''
    const budget = c.price_max ? `≤ ${formatPrice(c.price_max, c.transaction_type, '', t('listings.perMonth'))}` : ''
    return [c.transaction_type === 'rent' ? t('listings.rent') : t('listings.sale'), where + (q ? ` (${q})` : ''), beds,
            c.area_min ? `${c.area_min}+ m²` : '', budget].filter(Boolean).join(' · ')
  }

  return (
    <AuthedPage title={t('requests.title')}>
      <div className={`${sel ? 'hidden lg:flex' : 'flex'} flex-wrap items-center gap-2 mb-4`}>
        <p className="hidden sm:block text-sm text-muted-foreground mr-auto">{t('requests.subtitle')}</p>
        <button onClick={() => load(true)} disabled={syncing} className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50">
          <RefreshCw className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} /> {t('requests.refresh')}
        </button>
        <button onClick={() => setForm(EMPTY_FORM)} className="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm">
          <Plus className="w-4 h-4" /> {t('requests.add')}
        </button>
      </div>
      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 text-destructive text-sm p-3 mb-4">{error}</div>}

      {form && (
        <div className="rounded-xl border border-border bg-card p-4 mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-muted-foreground">{t('requests.client')}<input className={input} value={form.client_name} onChange={e => setForm({ ...form, client_name: e.target.value })} /></label>
          <label className="text-xs text-muted-foreground">{t('requests.type')}
            <select className={input} value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}>
              <option value="Квартира">{t('listings.apartment')}</option>
              <option value="Вилла">{t('listings.villa')}</option>
              <option value="">{t('listings.any')}</option>
            </select>
          </label>
          <label className="text-xs text-muted-foreground">{t('requests.location')}<input className={input} placeholder="Monaco, Cap Martin, Канны…" value={form.location} onChange={e => setForm({ ...form, location: e.target.value })} /></label>
          <label className="text-xs text-muted-foreground">{t('requests.bedrooms')}<input className={input} placeholder="2-3 спальни" value={form.bedrooms} onChange={e => setForm({ ...form, bedrooms: e.target.value })} /></label>
          <label className="text-xs text-muted-foreground">{t('requests.area')}<input className={input} placeholder="80-120" value={form.area} onChange={e => setForm({ ...form, area: e.target.value })} /></label>
          <label className="text-xs text-muted-foreground">{t('requests.budget')}<input className={input} inputMode="numeric" value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} /></label>
          <label className="text-xs text-muted-foreground">{t('requests.rentBudget')}<input className={input} inputMode="numeric" value={form.rent} onChange={e => setForm({ ...form, rent: e.target.value })} /></label>
          <label className="text-xs text-muted-foreground sm:col-span-2 lg:col-span-3">{t('requests.notes')}<input className={input} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>
          <div className="flex items-end gap-2">
            <button onClick={save} disabled={!form.client_name.trim()} className="rounded-md bg-primary text-primary-foreground px-3 py-2 text-sm disabled:opacity-50">{t('requests.save')}</button>
            <button onClick={() => setForm(null)} className="rounded-md border border-border px-3 py-2 text-sm">{t('requests.cancel')}</button>
          </div>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className={`${sel ? 'hidden lg:block' : ''} space-y-2 lg:max-h-[calc(100vh-200px)] lg:overflow-y-auto pr-1`}>
          {!items && <p className="text-muted-foreground">{t('requests.loading')}</p>}
          {items?.map(r => (
            <button key={r.id} onClick={() => setSelected(r.id)}
              className={`w-full text-left rounded-lg border p-3 transition-colors ${selected === r.id ? 'border-primary bg-primary/5' : 'border-border bg-card hover:bg-accent'}`}>
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground truncate">{r.name}</span>
                <span className="ml-auto text-xs rounded px-1.5 py-0.5 bg-muted text-muted-foreground">{r.criteria.source === 'sheet' ? t('requests.sheet') : t('requests.manual')}</span>
              </div>
              <div className="text-xs text-muted-foreground mt-1">{summary(r)}</div>
              {r.criteria.location_text && <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1 italic">{r.criteria.location_text}</div>}
              {r.criteria.location_unrecognized && <div className="text-xs text-amber-600 mt-0.5">{t('requests.unrecognized')}</div>}
              <div className="text-xs mt-1.5">
                <span className="font-semibold text-foreground">{r.match_count}</span> <span className="text-muted-foreground">{t('requests.matches')}</span>
                {r.new_count > 0 && <span className="ml-2 text-amber-600 font-semibold">+{r.new_count} {t('requests.newMatches')}</span>}
              </div>
            </button>
          ))}
        </div>

        <div className={`${sel ? '' : 'hidden lg:block'} lg:col-span-2`}>
          {!sel && <p className="text-muted-foreground">{t('requests.select')}</p>}
          {sel && (
            <>
              <button onClick={() => setSelected(null)} className="lg:hidden inline-flex items-center gap-1 text-sm text-muted-foreground mb-2">
                <ArrowLeft className="w-4 h-4" /> {t('requests.title')}
              </button>
              <div className="flex flex-wrap items-start gap-3 mb-3">
                <div>
                  <h2 className="text-lg font-semibold text-foreground">{sel.name}</h2>
                  <p className="text-sm text-muted-foreground">{summary(sel)}</p>
                  <p className="text-xs text-muted-foreground">
                    {sel.criteria.broker && <span className="mr-3">{t('requests.broker')}: {sel.criteria.broker}</span>}
                    {sel.criteria.deadline && <span>{t('requests.deadline')}: {sel.criteria.deadline}</span>}
                  </p>
                  {sel.criteria.notes && <p className="text-xs text-muted-foreground mt-1">{sel.criteria.notes}</p>}
                </div>
                <div className="ml-auto">
                  {sel.criteria.source === 'manual'
                    ? <button onClick={() => remove(sel.id)} className="inline-flex items-center gap-1 text-sm text-destructive"><Trash2 className="w-4 h-4" />{t('requests.remove')}</button>
                    : <span className="text-xs text-muted-foreground">{t('requests.editInSheet')}</span>}
                </div>
              </div>
              {!matches && <p className="text-muted-foreground">{t('listings.loading')}</p>}
              {matches && matches.length === 0 && <p className="text-muted-foreground">{t('requests.noMatches')}</p>}
              <div className="grid gap-2 sm:gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
                {matches?.map(p => <ListingCardView key={p.id} p={p} />)}
              </div>
            </>
          )}
        </div>
      </div>
    </AuthedPage>
  )
}

'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, ExternalLink, Check, X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import AuthedPage from '@/components/listings/AuthedPage'
import { formatPrice, heroUrl, pipelineApi, QUARTER_NAMES } from '@/lib/pipelineApi'

// Possible duplicates the matcher was not sure about: one agency listing (left)
// vs the property it might belong to (right). One tap each.
export default function ReviewPage() {
  const { t } = useLanguage()
  const [data, setData] = useState<{ total: number; items: any[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => pipelineApi.reviews().then(setData).catch(e => setError(e.message)), [])
  useEffect(() => { load() }, [load])

  const decide = async (id: string, decision: 'merge' | 'reject') => {
    setBusy(true)
    try {
      await pipelineApi.decideReview(id, decision)
      setData(d => d && { total: d.total - 1, items: d.items.filter(x => x.id !== id) })
      if ((data?.items.length ?? 0) <= 2) await load()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const item = data?.items[0]
  const side = (o: { img: string | null; fallback: string | null; title: string; price: number | null; tx: string; area: number | null;
                     beds: number | null; floor: number | null; building: string | null; place: string; who: string; url: string | null }) => (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="aspect-[4/3] bg-muted">
        {(o.img || o.fallback) && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={(o.img || o.fallback) as string} alt="" referrerPolicy="no-referrer" className="w-full h-full object-cover" />
        )}
      </div>
      <div className="p-3 space-y-1 text-sm">
        <div className="text-lg font-semibold">{formatPrice(o.price, o.tx, t('listings.onRequest'), t('listings.perMonth'))}</div>
        <div className="text-foreground line-clamp-2">{o.building || o.title}</div>
        <div className="text-muted-foreground">{o.place}</div>
        <div className="text-muted-foreground">
          {[o.beds === 0 ? t('listings.studio') : o.beds != null ? `${o.beds} ${t('listings.beds')}` : null,
            o.area ? `${Math.round(o.area)} m²` : null, o.floor != null ? `${t('listings.floor')} ${o.floor}` : null].filter(Boolean).join(' · ')}
        </div>
        <div className="text-xs text-muted-foreground line-clamp-2">{o.who}</div>
        {o.url && <a href={o.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary"><ExternalLink className="w-3 h-3" />{t('listings.open')}</a>}
      </div>
    </div>
  )

  return (
    <AuthedPage title={t('review.title')}>
      <Link href="/listings" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3">
        <ArrowLeft className="w-4 h-4" /> {t('listings.back')}
      </Link>
      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 text-destructive text-sm p-3 mb-4">{error}</div>}
      {!data && <p className="text-muted-foreground">{t('listings.loading')}</p>}
      {data && !item && <p className="text-muted-foreground">{t('review.none')}</p>}
      {item && (
        <>
          <p className="text-sm text-muted-foreground mb-3">{t('review.question')} <span className="ml-2">({data!.total} {t('review.left')})</span></p>
          <div className="grid grid-cols-2 gap-2 sm:gap-4 mb-4">
            {side({ img: heroUrl(item.hero_image_key), fallback: item.photo_url, title: item.listing_title, price: item.price_on_request ? null : item.price_at_source,
                    tx: item.transaction_type, area: item.living_area_sqm, beds: item.bedrooms, floor: item.floor, building: item.building_name,
                    place: QUARTER_NAMES[item.quarter] || item.quarter || '', who: item.agency_name || item.site_key, url: item.source_url })}
            {side({ img: heroUrl(item.candidate_hero), fallback: item.candidate_photo_url, title: item.candidate_title, price: item.candidate_price,
                    tx: item.transaction_type, area: item.candidate_area, beds: item.candidate_bedrooms, floor: item.candidate_floor,
                    building: item.candidate_building, place: [QUARTER_NAMES[item.candidate_quarter] || item.candidate_quarter, item.candidate_city].filter(Boolean).join(', '),
                    who: `${item.candidate_sources} × ${item.candidate_agencies || ''}`, url: item.candidate_url })}
          </div>
          <div className="flex gap-2 sticky bottom-2">
            <button disabled={busy} onClick={() => decide(item.id, 'merge')}
              className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 text-white py-3 font-medium disabled:opacity-50">
              <Check className="w-5 h-5" /> {t('review.same')}
            </button>
            <button disabled={busy} onClick={() => decide(item.id, 'reject')}
              className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-card py-3 font-medium disabled:opacity-50">
              <X className="w-5 h-5" /> {t('review.different')}
            </button>
          </div>
        </>
      )}
    </AuthedPage>
  )
}

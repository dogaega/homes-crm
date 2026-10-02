'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Building2 } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatPrice, heroUrl, QUARTER_NAMES, type ListingCard } from '@/lib/pipelineApi'

// Narrow screens: a compact row (photo left, facts right) so a screen shows
// 6–7 properties. Wider screens: a small tile in a 2–4 column grid.
export default function ListingCardView({ p }: { p: ListingCard }) {
  const { t } = useLanguage()
  const [heroFailed, setHeroFailed] = useState(false)
  const img = (!heroFailed && heroUrl(p.hero_image_key)) || p.photo_url
  const place = [p.city === 'Monaco' && p.quarter ? QUARTER_NAMES[p.quarter] || p.quarter : null, p.city].filter(Boolean).join(', ')
  // Some sites give no usable title ("Sale - Apartment - Monte-Carlo", "Detail d'une annonce").
  const junk = (x: string | null) => !x || x.length < 6 || /^(?:monaco|—|-)$|d[ée]tail d.une annonce|^(?:sale|vente|rent|location)\s*[-–]\s*(?:apartment|appartement)/i.test(x.trim())
  const kind = p.bedrooms === 0 ? t('listings.studio') : p.bedrooms != null ? `${p.bedrooms} ${t('listings.beds')}` : ''
  const title = !junk(p.building_name) ? p.building_name : !junk(p.property_name) ? p.property_name
    : [kind, p.living_area_sqm ? `${Math.round(p.living_area_sqm)} m²` : ''].filter(Boolean).join(' · ') || '—'
  const perM2 = p.price && p.living_area_sqm && p.living_area_sqm >= 10 && p.transaction_type === 'sale'
    ? `${Math.round(p.price / p.living_area_sqm / 1000)}k €/m²` : null
  const facts = [
    p.bedrooms === 0 ? t('listings.studio') : p.bedrooms != null ? `${p.bedrooms} ${t('listings.beds')}` : null,
    p.living_area_sqm != null ? `${Math.round(p.living_area_sqm)} m²` : null,
    p.floor != null ? `${t('listings.floor')} ${p.floor}` : null,
    perM2,
  ].filter(Boolean).join(' · ')

  return (
    <Link href={`/listings/${p.id}`}
      className="flex sm:flex-col gap-3 sm:gap-0 rounded-lg border border-border bg-card overflow-hidden hover:shadow-md transition-shadow">
      <div className="relative w-28 h-24 sm:w-full sm:h-auto sm:aspect-[4/3] shrink-0 bg-muted">
        {img ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={img} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setHeroFailed(true)}
               className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-muted-foreground"><Building2 className="w-6 h-6" /></div>
        )}
        {p.source_count > 1 && (
          <span className="absolute bottom-1 right-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-black/70 text-white">
            ×{p.source_count}
          </span>
        )}
      </div>
      <div className="min-w-0 py-2 pr-2 sm:p-3 space-y-0.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-base font-semibold text-foreground whitespace-nowrap">
            {formatPrice(p.price, p.transaction_type, t('listings.onRequest'), t('listings.perMonth'))}
          </span>
          {p.transaction_type === 'rent' && <span className="text-[10px] font-semibold px-1.5 rounded bg-sky-600 text-white">{t('listings.rent')}</span>}
          {p.is_new && <span className="text-[10px] font-semibold px-1.5 rounded bg-amber-500 text-white">{t('listings.isNew')}</span>}
          {p.contact_count > 0 && <span className="text-[10px] font-semibold px-1.5 rounded bg-violet-600 text-white">{t('listings.contacted')}</span>}
        </div>
        <div className="text-sm text-foreground truncate">{title}</div>
        <div className="text-xs text-muted-foreground truncate">{place}</div>
        {facts && <div className="text-xs text-muted-foreground">{facts}</div>}
      </div>
    </Link>
  )
}

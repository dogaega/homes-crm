'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Building2, BedDouble, Maximize, Layers } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatPrice, heroUrl, QUARTER_NAMES, type ListingCard } from '@/lib/pipelineApi'

export default function ListingCardView({ p }: { p: ListingCard }) {
  const { t } = useLanguage()
  const [heroFailed, setHeroFailed] = useState(false)
  const img = (!heroFailed && heroUrl(p.hero_image_key)) || p.photo_url
  const weekAgo = Date.now() - 7 * 86400_000
  const isNew = p.is_new ?? Date.parse(p.first_seen_at) > weekAgo
  const place = [p.city === 'Monaco' && p.quarter ? QUARTER_NAMES[p.quarter] || p.quarter : null, p.city].filter(Boolean).join(', ')
  return (
    <Link href={`/listings/${p.id}`} className="group block rounded-xl border border-border bg-card overflow-hidden hover:shadow-lg transition-shadow">
      <div className="relative aspect-[4/3] bg-muted">
        {img ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={img} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setHeroFailed(true)} className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-muted-foreground"><Building2 className="w-10 h-10" /></div>
        )}
        <div className="absolute top-2 left-2 flex gap-1">
          <span className={`text-xs font-semibold px-2 py-0.5 rounded ${p.transaction_type === 'rent' ? 'bg-sky-600 text-white' : 'bg-emerald-600 text-white'}`}>
            {p.transaction_type === 'rent' ? t('listings.rent') : t('listings.sale')}
          </span>
          {isNew && <span className="text-xs font-semibold px-2 py-0.5 rounded bg-amber-500 text-white">{t('listings.isNew')}</span>}
          {p.contact_count > 0 && <span className="text-xs font-semibold px-2 py-0.5 rounded bg-violet-600 text-white">{t('listings.contacted')}</span>}
        </div>
        {p.source_count > 1 && (
          <span className="absolute top-2 right-2 text-xs font-semibold px-2 py-0.5 rounded bg-black/70 text-white">
            {p.source_count} {t('listings.agencies')}
          </span>
        )}
      </div>
      <div className="p-3 space-y-1">
        <div className="text-lg font-semibold text-foreground">
          {formatPrice(p.price, p.transaction_type, t('listings.onRequest'), t('listings.perMonth'))}
        </div>
        <div className="text-sm text-foreground line-clamp-1">{p.building_name || p.property_name || '—'}</div>
        <div className="text-xs text-muted-foreground">{place}</div>
        <div className="flex gap-3 text-xs text-muted-foreground pt-1">
          {p.bedrooms != null && <span className="flex items-center gap-1"><BedDouble className="w-3.5 h-3.5" />{p.bedrooms} {t('listings.beds')}</span>}
          {p.living_area_sqm != null && <span className="flex items-center gap-1"><Maximize className="w-3.5 h-3.5" />{Math.round(p.living_area_sqm)} m²</span>}
          {p.floor != null && <span className="flex items-center gap-1"><Layers className="w-3.5 h-3.5" />{p.floor}</span>}
        </div>
      </div>
    </Link>
  )
}

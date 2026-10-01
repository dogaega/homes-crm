'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, ExternalLink, Phone, Mail, MessageCircle, Check } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import AuthedPage from '@/components/listings/AuthedPage'
import { formatPrice, heroUrl, pipelineApi, QUARTER_NAMES } from '@/lib/pipelineApi'

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleDateString('fr-FR') : '—')
const tel = (s: string) => s.replace(/[^\d+]/g, '')

export default function ListingDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t } = useLanguage()
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [heroFailed, setHeroFailed] = useState(false)

  const load = useCallback(() => pipelineApi.listing(id).then(setData).catch(e => setError(e.message)), [id])
  useEffect(() => { load() }, [load])

  const mark = async (sourceId?: string) => {
    setBusy(sourceId || 'all')
    try { await pipelineApi.markContacted(id, sourceId); await load() } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }

  const p = data?.property
  const live = (data?.sources || []).filter((s: any) => !s.removed_at)
  const photos: string[] = live.find((s: any) => s.photo_urls?.length)?.photo_urls?.slice(0, 12) || []
  const markedSources = new Set((data?.contact_marks || []).map((m: any) => m.source_id))
  const place = p ? [p.city === 'Monaco' && p.quarter ? QUARTER_NAMES[p.quarter] || p.quarter : null, p.city].filter(Boolean).join(', ') : ''

  return (
    <AuthedPage title={t('listings.title')}>
      <Link href="/listings" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4">
        <ArrowLeft className="w-4 h-4" /> {t('listings.back')}
      </Link>
      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 text-destructive text-sm p-3 mb-4">{error}</div>}
      {!data && !error && <p className="text-muted-foreground">{t('listings.loading')}</p>}

      {p && (
        <div className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-5">
            <div className="lg:col-span-3 rounded-xl overflow-hidden bg-muted aspect-[4/3]">
              {(!heroFailed && heroUrl(p.hero_image_key)) || photos[0] ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={(!heroFailed && heroUrl(p.hero_image_key)) || photos[0]} alt="" referrerPolicy="no-referrer"
                     onError={() => setHeroFailed(true)} className="w-full h-full object-cover" />
              ) : null}
            </div>
            <div className="lg:col-span-2 space-y-3">
              <div className="flex gap-2">
                <span className={`text-xs font-semibold px-2 py-0.5 rounded ${p.transaction_type === 'rent' ? 'bg-sky-600 text-white' : 'bg-emerald-600 text-white'}`}>
                  {p.transaction_type === 'rent' ? t('listings.rent') : t('listings.sale')}
                </span>
                <span className="text-xs text-muted-foreground">{p.property_id}</span>
              </div>
              <h2 className="text-2xl font-semibold text-foreground">{formatPrice(p.price, p.transaction_type, t('listings.onRequest'), t('listings.perMonth'))}</h2>
              <p className="text-foreground">{p.building_name || p.property_name}</p>
              <p className="text-sm text-muted-foreground">{place}</p>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                {p.bedrooms != null && <><dt className="text-muted-foreground">{t('listings.bedrooms')}</dt><dd>{p.bedrooms}</dd></>}
                {p.living_area_sqm != null && <><dt className="text-muted-foreground">m²</dt><dd>{Math.round(p.living_area_sqm)}</dd></>}
                {p.terrace_sqm != null && <><dt className="text-muted-foreground">{t('listings.terrace')}</dt><dd>{Math.round(p.terrace_sqm)} m²</dd></>}
                {p.floor != null && <><dt className="text-muted-foreground">{t('listings.floor')}</dt><dd>{p.floor}</dd></>}
                <dt className="text-muted-foreground">{t('listings.firstSeen')}</dt><dd>{fmtDate(p.first_seen_at)}</dd>
                <dt className="text-muted-foreground">{t('listings.lastSeen')}</dt><dd>{fmtDate(p.last_seen_at)}</dd>
              </dl>
              <button onClick={() => mark()} disabled={busy !== null}
                className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground px-3 py-2 text-sm disabled:opacity-50">
                <Check className="w-4 h-4" /> {t('listings.markContacted')}
              </button>
            </div>
          </div>

          <section>
            <h3 className="font-semibold text-foreground mb-2">{t('listings.agencyListings')} ({live.length})</h3>
            <div className="overflow-x-auto rounded-xl border border-border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-muted-foreground">
                  <tr>
                    <th className="text-left p-2">{t('listings.agency')}</th>
                    <th className="text-left p-2">{t('listings.price')}</th>
                    <th className="text-left p-2">{t('listings.agent')}</th>
                    <th className="text-left p-2" />
                  </tr>
                </thead>
                <tbody>
                  {data.sources.map((s: any) => {
                    const phone = s.agent_phone || s.agency_phone || s.agency_main_phone
                    const email = s.agent_email || s.agency_email || s.agency_main_email
                    return (
                      <tr key={s.id} className={`border-t border-border ${s.removed_at ? 'opacity-50' : ''}`}>
                        <td className="p-2">
                          <div className="font-medium text-foreground">{s.agency_name || s.site_key}</div>
                          <div className="text-xs text-muted-foreground">{s.external_ref || ''} {s.removed_at ? `· ${t('listings.removed')} ${fmtDate(s.removed_at)}` : ''}</div>
                        </td>
                        <td className="p-2 whitespace-nowrap">{formatPrice(s.price_on_request ? null : s.price_at_source, s.transaction_type, t('listings.onRequest'), t('listings.perMonth'))}</td>
                        <td className="p-2">
                          {s.agent_name && <div className="text-foreground">{s.agent_name}</div>}
                          <div className="flex flex-wrap gap-2 text-xs">
                            {phone && <a href={`tel:${tel(phone)}`} className="inline-flex items-center gap-1 text-primary"><Phone className="w-3 h-3" />{phone}</a>}
                            {s.agent_whatsapp && <a href={`https://wa.me/${tel(s.agent_whatsapp).replace('+', '')}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary"><MessageCircle className="w-3 h-3" />WhatsApp</a>}
                            {email && <a href={`mailto:${email}`} className="inline-flex items-center gap-1 text-primary"><Mail className="w-3 h-3" />{email}</a>}
                          </div>
                        </td>
                        <td className="p-2 whitespace-nowrap text-right space-x-2">
                          <a href={s.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary text-xs"><ExternalLink className="w-3 h-3" />{t('listings.open')}</a>
                          {!s.removed_at && (markedSources.has(s.id)
                            ? <span className="text-xs text-violet-600">{t('listings.marked')}</span>
                            : <button onClick={() => mark(s.id)} disabled={busy !== null} className="text-xs underline text-foreground disabled:opacity-50">{t('listings.markContacted')}</button>)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {live[0]?.listing_description && (
            <section className="text-sm text-foreground whitespace-pre-line leading-relaxed max-w-3xl">{live[0].listing_description}</section>
          )}

          {photos.length > 1 && (
            <section>
              <h3 className="font-semibold text-foreground mb-2">{t('listings.photos')}</h3>
              <div className="grid gap-2 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
                {photos.map(u => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={u} src={u} alt="" loading="lazy" referrerPolicy="no-referrer" className="w-full aspect-[4/3] object-cover rounded-lg bg-muted" />
                ))}
              </div>
            </section>
          )}

          <div className="grid gap-6 md:grid-cols-2">
            {data.price_history.length > 0 && (
              <section>
                <h3 className="font-semibold text-foreground mb-2">{t('listings.priceHistory')}</h3>
                <ul className="text-sm space-y-1">
                  {data.price_history.map((h: any, i: number) => (
                    <li key={i} className="text-foreground">{fmtDate(h.observed_at)} · {h.previous_price != null ? `${formatPrice(h.previous_price, p.transaction_type, '—', '')} → ` : ''}{formatPrice(h.price, p.transaction_type, '—', '')}</li>
                  ))}
                </ul>
              </section>
            )}
            {data.contact_marks.length > 0 && (
              <section>
                <h3 className="font-semibold text-foreground mb-2">{t('listings.contactLog')}</h3>
                <ul className="text-sm space-y-1">
                  {data.contact_marks.map((m: any) => (
                    <li key={m.id} className="text-foreground">{fmtDate(m.created_at)} · {m.marked_by_name || '—'} · {data.sources.find((s: any) => s.id === m.source_id)?.agency_name || ''} {m.note || ''}</li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </div>
      )}
    </AuthedPage>
  )
}

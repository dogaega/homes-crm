'use client'

import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet.markercluster'
import 'leaflet/dist/leaflet.css'
import 'leaflet.markercluster/dist/MarkerCluster.css'
import 'leaflet.markercluster/dist/MarkerCluster.Default.css'
import { heroUrl } from '@/lib/pipelineApi'

export interface MapPoint {
  id: string; lat: number; lng: number; exact: boolean; price: number | null; tx: string; city: string | null
  quarter: string | null; beds: number | null; area: number | null; title: string | null; hero: string | null; is_new: boolean
}
export interface Bounds { s: number; w: number; n: number; e: number }

const short = (p: number | null, tx: string) => {
  if (p == null) return '—'
  if (tx === 'rent') return p >= 1000 ? `${Math.round(p / 1000)}k/m` : `${p}/m`
  return p >= 1e6 ? `${(p / 1e6).toFixed(p >= 1e7 ? 0 : 1)}M` : `${Math.round(p / 1000)}k`
}
const esc = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))

// Leaflet map with price pins. Exact positions (agency coordinates, known
// building) are dark; approximate ones (quarter / town centre) are grey.
export default function MapView({ points, onBounds, labels }: {
  points: MapPoint[]
  onBounds: (b: Bounds) => void
  labels: { open: string; approx: string; beds: string }
}) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.MarkerClusterGroup | null>(null)
  const boundsCb = useRef(onBounds)
  boundsCb.current = onBounds

  useEffect(() => {
    if (!el.current || map.current) return
    let start: { c: [number, number]; z: number } = { c: [43.7384, 7.4246], z: 14 }
    try { start = JSON.parse(localStorage.getItem('listings.map') || '') || start } catch { /* default: Monaco */ }
    const m = L.map(el.current, { zoomControl: true }).setView(start.c, start.z)
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors',
    }).addTo(m)
    layer.current = L.markerClusterGroup({ maxClusterRadius: 40, showCoverageOnHover: false, spiderfyOnMaxZoom: true }).addTo(m)
    const report = () => {
      const b = m.getBounds()
      boundsCb.current({ s: b.getSouth(), w: b.getWest(), n: b.getNorth(), e: b.getEast() })
      try { localStorage.setItem('listings.map', JSON.stringify({ c: [m.getCenter().lat, m.getCenter().lng], z: m.getZoom() })) } catch { /* private mode */ }
    }
    m.on('moveend', report)
    map.current = m
    report()
    return () => { m.remove(); map.current = null }
  }, [])

  useEffect(() => {
    const g = layer.current
    if (!g) return
    g.clearLayers()
    g.addLayers(points.map(p => {
      const icon = L.divIcon({
        className: '',
        html: `<div style="transform:translate(-50%,-50%);white-space:nowrap;padding:2px 6px;border-radius:10px;font:600 11px system-ui;
               color:#fff;background:${p.exact ? (p.tx === 'rent' ? '#0369a1' : '#111827') : '#9ca3af'};
               border:${p.is_new ? '2px solid #f59e0b' : '1px solid #fff'};box-shadow:0 1px 3px rgba(0,0,0,.3)">${short(p.price, p.tx)}</div>`,
        iconSize: [0, 0],
      })
      const img = heroUrl(p.hero)
      const facts = [p.beds != null ? `${p.beds} ${labels.beds}` : '', p.area ? `${Math.round(p.area)} m²` : ''].filter(Boolean).join(' · ')
      return L.marker([p.lat, p.lng], { icon }).bindPopup(
        `<div style="width:200px;font:13px system-ui">
           ${img ? `<img src="${img}" style="width:100%;height:110px;object-fit:cover;border-radius:6px" />` : ''}
           <div style="font-weight:600;margin-top:4px">${esc(short(p.price, p.tx))}${p.tx === 'rent' ? '' : ' €'}</div>
           <div>${esc(p.title || '')}</div>
           <div style="color:#6b7280">${esc([p.quarter, p.city].filter(Boolean).join(', '))}${facts ? ' · ' + facts : ''}</div>
           ${p.exact ? '' : `<div style="color:#9ca3af;font-size:11px">${esc(labels.approx)}</div>`}
           <a href="/listings/${p.id}" style="color:#2563eb">${esc(labels.open)} →</a>
         </div>`)
    }))
  }, [points, labels])

  return <div ref={el} className="w-full h-full rounded-lg overflow-hidden" />
}

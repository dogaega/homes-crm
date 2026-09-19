// Brochure HTML template — matches the Monaco Riviera public-site design
// system (Cormorant Garamond display serif, Inter sans, cream background,
// warm gold accent) and the 5-page reference brochure structure:
// cover / description+characteristics / interior gallery / private-zone
// gallery / location+DPE gauge.

export type Lang = 'ru' | 'en'

const DICT = {
  ru: {
    longTermRental: 'ДОЛГОСРОЧНАЯ АРЕНДА',
    saleLabel: 'ПРОДАЖА',
    perMonth: '/ месяц',
    area: 'ОБЩАЯ ПЛОЩАДЬ',
    bedroom: 'СПАЛЬНЯ',
    bedrooms: 'СПАЛЬНИ',
    rooms: 'КОМНАТЫ',
    parking: 'ПАРКОВКА',
    objectEyebrow: 'ОБЪЕКТ',
    descHeading: 'Описание и характеристики',
    periodLabel: 'ПЕРИОД АРЕНДЫ',
    priceLabel: 'ЦЕНА',
    characteristics: 'ХАРАКТЕРИСТИКИ',
    propertyType: 'ТИП ОБЪЕКТА',
    rentalType: 'ТИП АРЕНДЫ',
    roomCount: 'КОЛИЧЕСТВО КОМНАТ',
    bedroomsLbl: 'СПАЛЬНИ',
    bathrooms: 'ВАННЫЕ КОМНАТЫ',
    totalArea: 'ОБЩАЯ ПЛОЩАДЬ',
    parkingSpaces: 'ПАРКОВОЧНЫЕ МЕСТА',
    condition: 'СОСТОЯНИЕ',
    view: 'ВИД',
    district: 'РАЙОН',
    city: 'ГОРОД',
    interiorEyebrow: 'ИНТЕРЬЕР',
    livingKitchenHeading: 'Гостиная и кухня',
    privateEyebrow: 'ПРИВАТНАЯ ЗОНА',
    bedroomBathHeading: 'Спальня и ванная комната',
    locationEyebrow: 'ЛОКАЦИЯ',
    locationHeading: 'Терраса и вид на море',
    locationLabel: 'РАСПОЛОЖЕНИЕ',
    monaco: 'МОНАКО',
    terrace: 'ТЕРРАСА',
    energyEfficiency: 'ЭНЕРГОЭФФЕКТИВНОСТЬ (DPE)',
    ref: '№',
  },
  en: {
    longTermRental: 'LONG-TERM RENTAL',
    saleLabel: 'FOR SALE',
    perMonth: '/ month',
    area: 'TOTAL AREA',
    bedroom: 'BEDROOM',
    bedrooms: 'BEDROOMS',
    rooms: 'ROOMS',
    parking: 'PARKING',
    objectEyebrow: 'PROPERTY',
    descHeading: 'Description & Characteristics',
    periodLabel: 'RENTAL PERIOD',
    priceLabel: 'PRICE',
    characteristics: 'CHARACTERISTICS',
    propertyType: 'PROPERTY TYPE',
    rentalType: 'RENTAL TYPE',
    roomCount: 'ROOM COUNT',
    bedroomsLbl: 'BEDROOMS',
    bathrooms: 'BATHROOMS',
    totalArea: 'TOTAL AREA',
    parkingSpaces: 'PARKING SPACES',
    condition: 'CONDITION',
    view: 'VIEW',
    district: 'DISTRICT',
    city: 'CITY',
    interiorEyebrow: 'INTERIOR',
    livingKitchenHeading: 'Living Room & Kitchen',
    privateEyebrow: 'PRIVATE AREA',
    bedroomBathHeading: 'Bedroom & Bathroom',
    locationEyebrow: 'LOCATION',
    locationHeading: 'Terrace & Sea View',
    locationLabel: 'LOCATION',
    monaco: 'MONACO',
    terrace: 'TERRACE',
    energyEfficiency: 'ENERGY EFFICIENCY (DPE)',
    ref: 'No.',
  },
} as const

export interface BrochureProperty {
  property_name?: string | null
  address: string
  city: string
  description?: string | null
  property_type?: string | null
  price?: number | null
  listing_type_label?: string | null
  rental_type?: string | null
  bedrooms?: number | null
  bathrooms?: number | null
  room_count?: number | null
  parking_spaces?: number | null
  square_feet?: number | null
  condition_rating?: string | null
  view_description?: string | null
  district?: string | null
  distance_to_monaco?: string | null
  terrace_description?: string | null
  availability_period?: string | null
  reference_number?: string | null
  dpe_rating?: string | null
  gallery_urls?: string[] | string | null
  photos?: string[] | string | null
}

function esc(s: unknown): string {
  if (s == null) return ''
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string))
}

function toArray(v: string[] | string | null | undefined): string[] {
  if (!v) return []
  if (Array.isArray(v)) return v.filter(Boolean)
  try {
    const parsed = JSON.parse(v)
    if (Array.isArray(parsed)) return parsed.filter(Boolean)
  } catch { /* not JSON, treat as single url */ }
  return v ? [v] : []
}

function photoBlock(url: string | undefined, className: string, alt: string): string {
  // onerror swaps a failed/slow-to-load photo (a real network hiccup fetching
  // an external image inside the render sandbox, not just a missing URL) to
  // the same graceful placeholder used when there's no URL at all — never
  // leave a broken-image icon in a brochure meant to go out to clients.
  if (url) {
    return `<div class="${className}"><img src="${esc(url)}" alt="${esc(alt)}" onerror="this.closest('.${className.split(' ')[0]}').classList.add('placeholder');this.remove();this.closest('.${className.split(' ')[0]}').innerHTML='<span>—</span>'" /></div>`
  }
  return `<div class="${className} placeholder"><span>—</span></div>`
}

function dpeGauge(rating: string | null | undefined): string {
  const letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
  const colors = ['#2ecc71', '#7fc243', '#c9d33a', '#f4d128', '#f0a83c', '#e8722e', '#e0322c']
  const widths = [60, 80, 100, 120, 140, 160, 180]
  const rowH = 22
  const gap = 3
  const rating_ = (rating || '').toUpperCase()
  const idx = letters.indexOf(rating_)
  let y = 0
  const rows = letters.map((l, i) => {
    const w = widths[i]
    const row = `<g transform="translate(0,${y})">
      <path d="M0,0 H${w} L${w + 10},${rowH / 2} L${w},${rowH} H0 Z" fill="${colors[i]}" />
      <text x="8" y="${rowH / 2 + 5}" font-family="Inter, sans-serif" font-size="12" font-weight="700" fill="#1c1a18">${l}</text>
    </g>`
    y += rowH + gap
    return row
  }).join('')
  let pointer = ''
  if (idx >= 0) {
    const py = idx * (rowH + gap) + rowH / 2
    const w = widths[idx]
    pointer = `<g transform="translate(${w + 18},${py})">
      <path d="M0,-11 H40 L50,0 L40,11 H0 Z" fill="#f7f5f3" stroke="#1c1a18" stroke-width="1.5" />
    </g>`
  }
  const totalH = y - gap
  return `<svg viewBox="0 0 260 ${totalH}" width="260" height="${totalH}" xmlns="http://www.w3.org/2000/svg">${rows}${pointer}</svg>`
}

export function renderBrochureHtml(p: BrochureProperty, lang: Lang): string {
  const t = DICT[lang]
  const gallery = [...toArray(p.gallery_urls), ...toArray(p.photos)]
  // This template has ~12 photo slots across 5 pages, but a real listing
  // often has far fewer photos (sometimes just 1-3). Rather than a mostly
  // empty-placeholder brochure, cycle through whatever photos exist —
  // repeating them as needed — so every slot shows a real photo as long as
  // at least one exists; only a genuinely photo-less property (0 URLs)
  // falls back to the blank placeholder everywhere.
  const pick = (index: number) => (gallery.length > 0 ? gallery[index % gallery.length] : undefined)
  const cover = pick(0)
  const descPhoto = pick(1)
  const interior = [pick(2), pick(3), pick(4), pick(5)]
  const privatePhotos = [pick(6), pick(7), pick(8), pick(9)]
  const locMain = pick(10)
  const locSecondary = pick(11)

  const name = p.property_name || p.address
  const priceStr = p.price != null ? `${Number(p.price).toLocaleString(lang === 'ru' ? 'ru-RU' : 'en-US')} €` : ''
  // Whether this reads as a rental ("/ month") or a sale price: infer from
  // fields that only make sense for a rental, since the schema doesn't have
  // an explicit sale/rental flag — a sale listing wouldn't set these.
  const isRental = p.rental_type ? p.rental_type.toLowerCase() === 'rent' : Boolean(p.availability_period)
  const areaStr = p.square_feet != null ? `${p.square_feet} м²` : ''

  const interiorGrid = [0, 1, 2, 3].map((i) => photoBlock(interior[i], 'grid-photo', 'interior')).join('')
  const privateGrid = [0, 1, 2, 3].map((i) => photoBlock(privatePhotos[i], 'grid-photo', 'private')).join('')

  const charRows = ([
    [t.propertyType, p.property_type],
    [t.rentalType, p.rental_type],
    [t.roomCount, p.room_count != null ? String(p.room_count) : null],
    [t.bedroomsLbl, p.bedrooms != null ? String(p.bedrooms) : null],
    [t.bathrooms, p.bathrooms != null ? String(p.bathrooms) : null],
    [t.totalArea, areaStr || null],
    [t.parkingSpaces, p.parking_spaces != null ? String(p.parking_spaces) : null],
    [t.condition, p.condition_rating],
    [t.view, p.view_description],
    [t.district, p.district],
    [t.city, p.city ? `${p.city}${p.city.includes(',') ? '' : ', France'}` : null],
  ] as Array<[string, string | null | undefined]>).filter(([, v]) => v)

  const locRows = ([
    [t.monaco, p.distance_to_monaco],
    [t.district, p.district],
    [t.view, p.view_description],
    [t.terrace, p.terrace_description],
  ] as Array<[string, string | null | undefined]>).filter(([, v]) => v)

  const descParas = (p.description || '').split(/\n+/).filter(Boolean)

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
<meta charset="utf-8" />
<style>
  @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=Inter:wght@400;500;600;700&display=swap');
  :root {
    --background: #f7f5f3;
    --foreground: #1c1a18;
    --accent: #9c7b52;
    --muted: #8a8278;
    --line: #e3ddd5;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', system-ui, sans-serif; color: var(--foreground); background: var(--background); }
  .font-display { font-family: 'Cormorant Garamond', Georgia, serif; }
  .label { font-family: 'Inter', sans-serif; font-size: 10px; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase; color: var(--accent); }
  .page { width: 210mm; height: 297mm; position: relative; overflow: hidden; page-break-after: always; background: var(--background); }
  .page:last-child { page-break-after: auto; }

  /* Cover */
  .cover-wrap { position: relative; width: 100%; height: 170mm; overflow: hidden; }
  .cover-photo { width: 100%; height: 170mm; position: relative; background: #ccc; overflow: hidden; }
  .cover-photo img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cover-photo.placeholder { display: flex; align-items: center; justify-content: center; background: var(--line); color: var(--muted); }
  .cover-overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: 24px 28px; background: linear-gradient(to top, rgba(0,0,0,0.55), rgba(0,0,0,0)); color: #fff; }
  .cover-overlay .loc-line { font-size: 10px; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase; margin-bottom: 8px; }
  .cover-overlay h1 { font-family: 'Cormorant Garamond', serif; font-size: 48px; font-weight: 500; line-height: 1.05; }
  .cover-overlay .subtitle { font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 17px; margin-top: 6px; }
  .cover-overlay .rule { width: 48px; height: 2px; background: var(--accent); margin-top: 14px; }

  .spec-bar { padding: 22px 28px; display: flex; align-items: flex-start; gap: 20px; border-bottom: 1px solid var(--line); }
  .spec-price-col { min-width: 130px; }
  .spec-price-col .rlabel { font-size: 9px; font-weight: 600; letter-spacing: 0.16em; color: var(--accent); }
  .spec-price-col .price { font-family: 'Cormorant Garamond', serif; font-size: 30px; font-weight: 500; }
  .spec-price-col .price small { font-size: 13px; font-weight: 400; color: var(--muted); }
  .stat-cols { display: flex; gap: 26px; flex: 1; padding-top: 4px; }
  .stat-col { text-align: center; }
  .stat-col .val { font-family: 'Cormorant Garamond', serif; font-size: 22px; font-weight: 500; }
  .stat-col .slabel { font-size: 8px; font-weight: 600; letter-spacing: 0.12em; color: var(--muted); margin-top: 2px; }
  .spec-desc { flex: 1.4; font-size: 11px; line-height: 1.5; color: var(--foreground); padding-top: 4px; }
  .spec-avail { min-width: 140px; text-align: right; font-size: 9px; font-weight: 600; letter-spacing: 0.1em; color: var(--accent); line-height: 1.8; padding-top: 4px; }

  /* Inner pages */
  .content-page { padding: 30px 32px; }
  .eyebrow { margin-bottom: 6px; }
  h2.heading { font-family: 'Cormorant Garamond', serif; font-size: 30px; font-weight: 500; padding-bottom: 14px; border-bottom: 1px solid var(--line); margin-bottom: 18px; }

  .main-photo { width: 100%; height: 90mm; background: #ccc; overflow: hidden; margin-bottom: 8px; }
  .main-photo img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .main-photo.placeholder, .grid-photo.placeholder, .loc-secondary.placeholder { display: flex; align-items: center; justify-content: center; background: var(--line); color: var(--muted); font-size: 11px; }
  .caption { font-size: 9px; font-weight: 600; letter-spacing: 0.1em; color: var(--muted); text-transform: uppercase; margin-bottom: 16px; }

  .two-col { display: flex; gap: 24px; }
  .col-desc { flex: 1.3; font-size: 11.5px; line-height: 1.6; }
  .col-desc p { margin-bottom: 12px; }
  .callout { background: #efe9df; border-left: 3px solid var(--accent); padding: 12px 16px; margin-top: 16px; }
  .callout .clabel { font-size: 8.5px; font-weight: 700; letter-spacing: 0.12em; color: var(--accent); margin-bottom: 6px; }
  .callout .cvalue { font-family: 'Cormorant Garamond', serif; font-size: 20px; }
  .col-char { flex: 1; }
  .char-title { font-size: 9px; font-weight: 700; letter-spacing: 0.12em; color: var(--accent); margin-bottom: 10px; }
  .char-row { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 10.5px; }
  .char-row .k { color: var(--muted); letter-spacing: 0.05em; text-transform: uppercase; font-size: 9px; }
  .char-row .v { font-weight: 600; }

  .gallery-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
  .grid-photo { height: 55mm; background: #ccc; overflow: hidden; }
  .grid-photo img { width: 100%; height: 100%; object-fit: cover; display: block; }

  .loc-photos { display: flex; gap: 10px; margin-top: 4px; }
  .loc-main { flex: 1.6; height: 90mm; background: #ccc; overflow: hidden; }
  .loc-main img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .loc-secondary { flex: 1; }
  .loc-side { flex: 1; }
  .loc-secondary { height: 55mm; background: #ccc; overflow: hidden; }
  .loc-secondary img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .loc-info { flex: 1; padding-left: 4px; }
  .loc-row { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 10.5px; }
  .loc-row .k { color: var(--muted); font-size: 9px; letter-spacing: 0.05em; text-transform: uppercase; }
  .loc-row .v { font-weight: 600; }
  .loc-flex { display: flex; gap: 24px; margin-top: 20px; }

  .footer { position: absolute; bottom: 26px; left: 32px; right: 32px; display: flex; justify-content: space-between; align-items: baseline; padding-top: 12px; border-top: 1px solid var(--line); font-size: 10px; }
  .footer .fname { font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 14px; }
  .ref-badge { position: absolute; top: 24px; right: 32px; font-size: 9px; font-weight: 600; letter-spacing: 0.08em; color: var(--muted); }
</style>
</head>
<body>

<!-- PAGE 1: Cover -->
<section class="page">
  <div class="cover-wrap">
    ${photoBlock(cover, 'cover-photo', name)}
    <div class="cover-overlay">
      <div class="loc-line">${esc((p.district ? p.district.toUpperCase() + ' · ' : '') + p.city.toUpperCase() + (p.city.toLowerCase() === 'monaco' ? '' : ' · FRANCE'))}</div>
      <h1>${esc(name)}</h1>
      ${p.description ? `<div class="subtitle">${esc(descParas[0] || '').slice(0, 90)}</div>` : ''}
      <div class="rule"></div>
    </div>
  </div>
  <div class="spec-bar">
    <div class="spec-price-col">
      <div class="rlabel">${esc(p.listing_type_label || (isRental ? t.longTermRental : t.saleLabel))}</div>
      <div class="price">${esc(priceStr)} ${priceStr && isRental ? `<small>${esc(t.perMonth)}</small>` : ''}</div>
    </div>
    <div class="stat-cols">
      ${areaStr ? `<div class="stat-col"><div class="val">${esc(areaStr)}</div><div class="slabel">${esc(t.area)}</div></div>` : ''}
      ${p.bedrooms != null ? `<div class="stat-col"><div class="val">${esc(p.bedrooms)}</div><div class="slabel">${esc(p.bedrooms === 1 ? t.bedroom : t.bedrooms)}</div></div>` : ''}
      ${p.room_count != null ? `<div class="stat-col"><div class="val">${esc(p.room_count)}</div><div class="slabel">${esc(t.rooms)}</div></div>` : ''}
      ${p.parking_spaces != null ? `<div class="stat-col"><div class="val">${esc(p.parking_spaces)}</div><div class="slabel">${esc(t.parking)}</div></div>` : ''}
    </div>
    <div class="spec-desc">${esc(descParas[0] || p.description || '')}</div>
    <div class="spec-avail">
      ${p.availability_period ? esc(p.availability_period) + '<br/>' : ''}
      ${p.parking_spaces ? esc(lang === 'ru' ? 'ПАРКОВКА В РЕЗИДЕНЦИИ' : 'PARKING ON SITE') : ''}
    </div>
  </div>
</section>

<!-- PAGE 2: Description & Characteristics -->
<section class="page content-page">
  <div class="label eyebrow">${t.objectEyebrow}</div>
  <h2 class="heading">${t.descHeading}</h2>
  ${photoBlock(descPhoto, 'main-photo', name)}
  <div class="caption">${esc(lang === 'ru' ? 'ГОСТИНАЯ С ВЫХОДОМ НА ТЕРРАСУ' : 'LIVING ROOM WITH TERRACE ACCESS')}</div>
  <div class="two-col">
    <div class="col-desc">
      ${descParas.map((para) => `<p>${esc(para)}</p>`).join('') || `<p>${esc(p.description || '')}</p>`}
      ${p.availability_period || priceStr ? `<div class="callout">
        <div class="clabel">${isRental ? t.periodLabel : t.priceLabel}</div>
        <div class="cvalue">${esc(p.availability_period || '')}${p.availability_period && priceStr ? ' · ' : ''}${priceStr ? esc(priceStr) + (isRental ? ' ' + esc(t.perMonth) : '') : ''}</div>
      </div>` : ''}
    </div>
    <div class="col-char">
      <div class="char-title">${t.characteristics}</div>
      ${charRows.map(([k, v]) => `<div class="char-row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('')}
    </div>
  </div>
</section>

<!-- PAGE 3: Interior gallery -->
<section class="page content-page">
  <div class="label eyebrow">${t.interiorEyebrow}</div>
  <h2 class="heading">${t.livingKitchenHeading}</h2>
  ${photoBlock(interior[0] || gallery[1], 'main-photo', 'interior')}
  <div class="gallery-grid">${interiorGrid}</div>
  <div class="caption">${esc(lang === 'ru' ? 'ОТКРЫТАЯ КУХНЯ · ОБЕДЕННАЯ ЗОНА · ЗОНА ГОСТИНОЙ' : 'OPEN KITCHEN · DINING AREA · LIVING AREA')}</div>
</section>

<!-- PAGE 4: Private zone gallery -->
<section class="page content-page">
  <div class="label eyebrow">${t.privateEyebrow}</div>
  <h2 class="heading">${t.bedroomBathHeading}</h2>
  ${photoBlock(privatePhotos[0] || gallery[6], 'main-photo', 'bedroom')}
  <div class="gallery-grid">${privateGrid}</div>
  <div class="caption">${esc(lang === 'ru' ? 'СПАЛЬНЯ · ВАННАЯ КОМНАТА · ХОЛЛ · ТЕРРАСА' : 'BEDROOM · BATHROOM · HALLWAY · TERRACE')}</div>
</section>

<!-- PAGE 5: Location -->
<section class="page content-page">
  ${p.reference_number ? `<div class="ref-badge">${t.ref} ${esc(p.reference_number)}</div>` : ''}
  <div class="label eyebrow">${t.locationEyebrow}</div>
  <h2 class="heading">${t.locationHeading}</h2>
  ${photoBlock(locMain, 'loc-main', 'terrace')}
  <div class="loc-flex">
    <div class="loc-side">${photoBlock(locSecondary, 'loc-secondary', 'view')}</div>
    <div class="loc-info">
      ${locRows.length ? `<div class="char-title">${t.locationLabel}</div>
      ${locRows.map(([k, v]) => `<div class="loc-row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('')}` : ''}
      ${p.dpe_rating ? `<div class="char-title" style="margin-top:18px;">${t.energyEfficiency}</div>${dpeGauge(p.dpe_rating)}` : ''}
    </div>
  </div>
  <div class="footer">
    <div class="fname">${esc(name)} · ${esc(p.city)}, France</div>
    ${p.reference_number ? `<div>${t.ref} ${esc(p.reference_number)}</div>` : ''}
  </div>
</section>

</body>
</html>`
}

export function brochureFilename(p: BrochureProperty, lang: Lang): string {
  const name = (p.property_name || p.address || 'Property').replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const stamp = `${pad(now.getFullYear() % 100)}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `${name}_${lang.toUpperCase()}_${stamp}.pdf`
}

"""
Scrape montecarlo-realestate.com's Monaco sale listings and push each one
through the CRM's intake pipeline (Groq does the field extraction, not us —
this script only fetches pages and calls the API).

Usage: python3 scrape_mcre.py <token> <start_page> <end_page> [--approve]
"""
import sys, re, json, time, urllib.request, urllib.error
import stealth_requests as sreq

BASE = "https://www.montecarlo-realestate.com"
API = "https://monaco-riviera-crm-worker.markmirimsky-705.workers.dev"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

def fetch(url):
    r = sreq.get(url, timeout=25)
    r.raise_for_status()
    return r.text

def html_to_text(html):
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.S)
    html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&#x20AC;', '€', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def list_page_urls(page_num):
    url = f"{BASE}/en/houses-and-apartments-for-sale/monaco" + (f"?pag={page_num}" if page_num > 1 else "")
    html = fetch(url)
    urls = sorted(set(re.findall(r'href="(/en/properties/mc-tc-[^"]+)"', html)))
    cards = {}
    for m in re.finditer(r'card__title js_link_immobile">([^<]+)</a>\s*<span class="card__quartiere">([^<]+)</span>.*?card__info">([\d,]+)\s*sqm.*?card__price">([^<]+)<', html, re.S):
        pass
    return urls

def api(method, path, token, body=None, params=None, raw_body=None, content_type=None):
    url = API + path
    if params:
        url += '?' + '&'.join(f"{k}={v}" for k, v in params.items())
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    hdrs = {
        "Authorization": f"Bearer {token}",
        "User-Agent": HEADERS["User-Agent"],
    }
    hdrs["Content-Type"] = content_type or "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors='ignore')
        return {"error": f"HTTP {e.code}: {body_text[:300]}"}

def extract_photo_urls(html, ref):
    urls = sorted(set(re.findall(rf'data-src="(https://img\.montecarlo-realestate\.com/annunci/[^"]*A_{re.escape(ref)}_[^"]*)"', html)))
    # The card/list-page markup gives the "FB" (thumbnail, 200x200) size;
    # the detail page's own <img src> for the same photo IDs uses "IMM-M-C"
    # (1024x768). Swap to the high-res variant before downloading.
    urls = [re.sub(r'/annunci/[^/]+/', '/annunci/IMM-M-C/', u) for u in urls]
    return urls[:8]

def upload_photos(token, photo_urls):
    # Watermark removal via fixed-box LaMa inpainting produced visible
    # smudging on the wall/TV areas — worse than the watermark itself.
    # Shipping the real high-res photo with its small logo intact instead
    # of a fabricated blur. Revisit with proper per-pixel watermark
    # detection (alpha-blend reversal or a real template match) later.
    uploaded = []
    for i, purl in enumerate(photo_urls):
        try:
            img_bytes = sreq.get(purl, timeout=20).content
        except Exception:
            continue
        res = api('POST', '/photos/upload', token, raw_body=img_bytes, content_type='image/jpeg',
                   params={"filename": f"photo_{i}.jpg"})
        if 'url' in res:
            uploaded.append(API + res['url'])
    return uploaded

def process_listing(token, detail_url, approve):
    full_url = BASE + detail_url
    try:
        html = fetch(full_url)
    except Exception as e:
        return {"url": full_url, "error": f"fetch failed: {e}"}
    text = html_to_text(html)
    idx = text.find('Property description') if 'Property description' in text else text.find('Description')
    snippet = text[max(0, idx-200):idx+3000] if idx > 0 else text[:3000]

    ref_match = re.search(r'mc-tc-(\d+-\d+)', detail_url)
    ref = ref_match.group(1).replace('-', '_') if ref_match else None
    photo_urls = extract_photo_urls(html, ref) if ref else []

    up = api('POST', '/intake/upload', token, body={"raw_text": snippet}, params={"source_type": "url", "source_url": full_url})
    if 'id' not in up:
        return {"url": full_url, "error": f"upload failed: {up}"}
    doc_id = up['id']

    ext = api('POST', f'/intake/{doc_id}/extract', token)
    if 'extracted_fields' not in ext:
        return {"url": full_url, "doc_id": doc_id, "error": f"extract failed: {ext}"}

    result = {"url": full_url, "doc_id": doc_id, "fields": ext['extracted_fields'], "confidence": ext['confidence'], "source_photos_found": len(photo_urls)}

    if approve and ext['confidence'] in ('high', 'medium') and ext['extracted_fields'].get('address') and ext['extracted_fields'].get('city'):
        ap = api('POST', f'/intake/{doc_id}/approve', token, body={})
        if 'id' in ap:
            result['approved'] = ap['id']
            if photo_urls:
                uploaded = upload_photos(token, photo_urls)
                if uploaded:
                    patch = api('PATCH', f"/properties/{ap['id']}", token, body={"photos": json.dumps(uploaded)})
                    result['photos_uploaded'] = len(uploaded) if 'error' not in patch else f"patch failed: {patch}"
        else:
            result['approved'] = f"approve failed: {ap}"
    else:
        result['approved'] = None

    return result

if __name__ == '__main__':
    token = sys.argv[1]
    start_page = int(sys.argv[2])
    end_page = int(sys.argv[3])
    approve = '--approve' in sys.argv

    all_results = []
    for page in range(start_page, end_page + 1):
        try:
            urls = list_page_urls(page)
        except Exception as e:
            print(json.dumps({"page": page, "error": f"list fetch failed: {e}"}))
            continue
        print(json.dumps({"page": page, "found": len(urls)}), file=sys.stderr)
        for u in urls:
            res = process_listing(token, u, approve)
            all_results.append(res)
            print(json.dumps(res))
            time.sleep(2.5)

    print(json.dumps({"total_processed": len(all_results)}), file=sys.stderr)

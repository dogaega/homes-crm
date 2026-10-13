"""
Fill and submit an agency's contact / enquiry form (usually the one on the
listing page itself) with Mark's details and a message. Plain HTTP, like a
browser without JavaScript: hidden fields are kept, the visible fields are
recognised by name / type / label. Forms protected by a CAPTCHA are never
attempted (they need a person). Newsletter boxes are left unticked; only the
privacy / consent box a contact form requires is ticked.

    python -m outreach.form_filler URL --dry-run     # show how the form would be filled
"""

from __future__ import annotations

import argparse
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
      "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8"}
ME = {"first": "Mark", "last": "Mirimsky", "full": "Mark Mirimsky", "email": "markmirimsky@gmail.com", "phone": "+35796146814"}
CAPTCHA = re.compile(r"g-recaptcha|grecaptcha|recaptcha/api|h-captcha|hcaptcha|cf-turnstile|turnstile|friendlycaptcha|captcha", re.I)
OK_WORDS = re.compile(r"merci|thank|envoy[ée]|bien re[çc]u|been sent|success|message sent|mail_sent|we will (get back|contact)", re.I)
ERR_WORDS = re.compile(r"obligatoire|required|invalid|erreur|error|incorrect|validation_failed|spam", re.I)
HONEYPOT = re.compile(r"honey|hp_|_hp|website|url|homepage|fax|leave.*empty|ne pas remplir", re.I)


def _label(form, el) -> str:
    bits = [el.get("name") or "", el.get("id") or "", el.get("placeholder") or "", el.get("aria-label") or "", el.get("autocomplete") or ""]
    if el.get("id"):
        lab = form.find("label", attrs={"for": el["id"]})
        if lab:
            bits.append(lab.get_text(" ", strip=True))
    par = el.find_parent("label")
    if par:
        bits.append(par.get_text(" ", strip=True))
    return " ".join(bits).lower()


def pick_form(soup):
    forms = [f for f in soup.find_all("form") if f.find("textarea")]
    if not forms:
        return None
    # The enquiry form: the one with an email field and a textarea; prefer the biggest.
    forms.sort(key=lambda f: (bool(f.find("input", attrs={"type": "email"}) or f.find("input", attrs={"name": re.compile("mail", re.I)})),
                              len(f.find_all(["input", "textarea", "select"]))), reverse=True)
    return forms[0]


def fill(form, message: str, subject: str, tx: str | None = None) -> tuple[dict, list[str]]:
    data, unknown = {}, []
    radios_done = set()
    for el in form.find_all(["input", "textarea", "select"]):
        name = el.get("name")
        if not name or el.has_attr("disabled"):
            continue
        typ = (el.get("type") or ("textarea" if el.name == "textarea" else el.name)).lower()
        lab = _label(form, el)
        required = el.has_attr("required") or el.get("aria-required") == "true" or "*" in lab
        if typ in ("hidden",):
            data[name] = el.get("value", "")
        elif typ in ("submit", "button", "image", "reset", "file", "password"):
            if typ == "submit" and el.get("value") is not None and name:
                data.setdefault(name, el["value"])
        elif typ == "checkbox":
            if re.search(r"newsletter|alerte|alert|offres|offers|marketing|partenaire|partner|subscribe|inscri", lab):
                continue
            if required or re.search(r"rgpd|gdpr|consent|accept|privacy|confidentialit|donn[ée]es|conditions|cgu|j'accepte|agree", lab):
                data[name] = el.get("value", "on")
        elif typ == "radio":
            if name in radios_done:
                continue
            group = form.find_all("input", attrs={"type": "radio", "name": name})
            vals = [(g.get("value") or "", _label(form, g)) for g in group]
            choice = next((v for v, l in vals if re.search(r"\b(m|mr|monsieur|mister|sir)\b", (v + " " + l).lower())), None) \
                or next((v for v, l in vals if tx and re.search(tx, (v + " " + l).lower())), None) or vals[0][0]
            data[name] = choice
            radios_done.add(name)
        elif el.name == "select":
            opts = [(o.get("value") if o.get("value") is not None else o.get_text(strip=True), o.get_text(" ", strip=True).lower()) for o in el.find_all("option")]
            opts = [(v, t) for v, t in opts if v not in ("", "0", "-1") and not re.search(r"choisi|select|choose|--", t)]
            if not opts:
                continue
            want = r"\b(m|mr|monsieur)\b" if re.search(r"civilit|title|gender|salutation", lab) else (tx or "")
            data[name] = next((v for v, t in opts if want and re.search(want, t)), opts[0][0])
        elif typ == "textarea":
            data[name] = message
        elif typ == "email" or re.search(r"e-?mail|courriel", lab):
            data[name] = ME["email"]
        elif typ == "tel" or re.search(r"t[ée]l|phone|portable|mobile|gsm", lab):
            data[name] = ME["phone"]
        elif re.search(r"pr[ée]nom|first|given|fname", lab):
            data[name] = ME["first"]
        elif re.search(r"\bnom\b|last|surname|family|lname", lab) and not re.search(r"full|complet", lab):
            data[name] = ME["last"]
        elif re.search(r"name|nom|identit", lab):
            data[name] = ME["full"]
        elif re.search(r"sujet|subject|objet|title|titre", lab):
            data[name] = subject
        elif re.search(r"pays|country", lab):
            data[name] = "Cyprus"
        elif re.search(r"ville|city|town", lab):
            data[name] = "Limassol"
        elif HONEYPOT.search(lab) and not required:
            data[name] = ""
        elif required:
            unknown.append(lab[:60])
        else:
            data[name] = el.get("value", "")
    return data, unknown


def submit(url: str, message: str, subject: str = "Property enquiry", tx: str | None = None, dry_run: bool = False) -> tuple[bool, str]:
    """Return (ok, note). ok=False with a note when the form could not be sent (CAPTCHA, unknown required field, error page)."""
    s = requests.Session()
    s.headers.update(UA)
    r = s.get(url, timeout=30)
    if r.status_code >= 400:
        return False, f"page HTTP {r.status_code}"
    soup = BeautifulSoup(r.text, "html.parser")
    form = pick_form(soup)
    if form is None:
        return False, "no contact form on the page"
    if CAPTCHA.search(str(form)) or (CAPTCHA.search(r.text) and re.search(r"sitekey|data-sitekey|render=", r.text)):
        return False, "form has a CAPTCHA — needs a person"
    data, unknown = fill(form, message, subject, tx)
    if unknown:
        return False, "unknown required fields: " + "; ".join(unknown)
    if dry_run:
        return True, "would send " + str({k: (v[:40] + "…" if len(v) > 40 else v) for k, v in data.items()})
    cls = " ".join(form.get("class") or [])
    if "wpcf7-form" in cls and data.get("_wpcf7"):
        # Contact Form 7 answers its own REST endpoint with a clear status.
        api = urljoin(url, f"/wp-json/contact-form-7/v1/contact-forms/{data['_wpcf7']}/feedback")
        rr = s.post(api, files={k: (None, v) for k, v in data.items()}, headers={"Referer": url}, timeout=60)
        try:
            j = rr.json()
        except ValueError:
            return False, f"CF7 HTTP {rr.status_code}"
        return (j.get("status") == "mail_sent"), f"CF7 {j.get('status')}: {str(j.get('message'))[:120]}"
    action = urljoin(url, form.get("action") or url)
    method = (form.get("method") or "post").lower()
    rr = (s.get(action, params=data, timeout=60, headers={"Referer": url}) if method == "get"
          else s.post(action, data=data, timeout=60, headers={"Referer": url, "Origin": re.match(r"https?://[^/]+", url).group(0)}))
    body = rr.text[:20000]
    if rr.status_code >= 400:
        return False, f"HTTP {rr.status_code}"
    if OK_WORDS.search(body) and not re.search(r"(champ|field)[^<]{0,40}(obligatoire|required)", body, re.I):
        return True, f"HTTP {rr.status_code}, confirmation shown"
    if rr.headers.get("content-type", "").startswith("application/json") and ERR_WORDS.search(body):
        return False, "rejected: " + body[:160]
    return True, f"HTTP {rr.status_code}, sent (no confirmation text — site may confirm with JavaScript)"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(submit(a.url, "Hello, is this property still available? Thanks.", dry_run=a.dry_run))

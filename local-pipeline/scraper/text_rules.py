"""
Deterministic text rules shared by all scrapers (no LLM): number parsing
in French/English formats and keyword flags from titles/descriptions.
"""

from __future__ import annotations

import re

_NUM = re.compile(r"\d[\d\s  .,']*")


def parse_number(text: str | None) -> float | None:
    """'2 650 000 €' -> 2650000, '335.30 m²' -> 335.3, '60,5 m²' -> 60.5,
    '1,250,000' -> 1250000, 'Prix sur demande' -> None."""
    if not text:
        return None
    m = _NUM.search(text)
    if not m:
        return None
    s = re.sub(r"[\s  ']", "", m.group(0)).rstrip(".,")
    if "," in s and "." in s:  # both: the last one is the decimal mark
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif s.count(".") > 1 or re.search(r"\.\d{3}$", s):
        s = s.replace(".", "")  # thousands dots: 1.250.000
    try:
        n = float(s)
    except ValueError:
        return None
    return int(n) if n.is_integer() else n


_FLAGS = {
    "sea_view": r"vue\s+(?:panoramique\s+)?(?:sur\s+(?:la\s+)?)?mer|vue\s+mer|sea[\s-]?view|view\s+(?:of|over)\s+the\s+sea|vista\s+mare|вид\s+на\s+море",
    "cellar": r"\bcaves?\b|\bcellar\b|\bcantina\b",
    "parking": r"\bparkings?\b|\bgarage\b|\bbox\b|parking\s+space|posto\s+auto",
}
_FLAGS_RE = {k: re.compile(v, re.I) for k, v in _FLAGS.items()}
# "sans vue mer", "pas de cave" — negations close before the keyword.
_NEG = re.compile(r"(?:sans|pas\s+de|no|without)\s+(?:\w+\s+){0,2}$", re.I)


def keyword_flags(text: str) -> dict[str, bool]:
    out = {}
    for key, rx in _FLAGS_RE.items():
        for m in rx.finditer(text or ""):
            if not _NEG.search(text[max(0, m.start() - 25):m.start()]):
                out[key] = True
                break
    return out


# Listing status from a title. "Sold furnished" / "vendu meublé" describe the
# sale, they do not mean it is gone.
_GONE = re.compile(r"\b(?:rented|sold|vendu|vendue|lou[ée]e?|let agreed)\b"
                   r"(?!\s+(?:furnished|unfurnished|fully|with|as|meubl|non[- ]meubl|enti[eè]rement|avec|en\b|tel))", re.I)
_UNDER_OFFER = re.compile(r"\b(?:under offer|sous offre|sous compromis|offer accepted|offre accept[ée]e)\b", re.I)


def title_status(title: str | None) -> str | None:
    """'gone' (already sold/rented), 'under_offer', or None."""
    if not title:
        return None
    if _UNDER_OFFER.search(title):
        return "under_offer"
    if _GONE.search(title):
        return "gone"
    return None

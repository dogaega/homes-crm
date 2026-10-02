"""
Hero image: first real listing photo → WebP (max 1024 px) + 64-bit difference
hash for the Worker's photo-match dedup tier. Everything stays in memory
(the Mac runner must not write to disk).
"""

from __future__ import annotations

import io
import logging

from PIL import Image

from scraper.fetch import PoliteFetcher

log = logging.getLogger("hero")

MAX_SIDE = 1024   # cards and the detail view never show it larger; ~4× lighter than 1600
QUALITY = 78


def dhash64(img: Image.Image) -> str:
    """Difference hash: robust to resizing/recompression, which is exactly
    how the same photo differs between two agencies' sites."""
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def make_hero(data: bytes) -> tuple[bytes, str]:
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    phash = dhash64(img)
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=QUALITY, method=4)
    return out.getvalue(), phash


GALLERY = 6


def gallery_hashes(f: PoliteFetcher, photo_urls: list[str], n: int = GALLERY) -> list[str]:
    """dHash of the first n photos, for the Worker's gallery matching. Blank or
    flat images (logos on white, placeholders) are skipped: they match anything."""
    out: list[str] = []
    for url in photo_urls[:n]:
        try:
            img = Image.open(io.BytesIO(f.get_bytes(url)))
            img.load()
            h = dhash64(img)
        except Exception as e:
            log.debug("gallery photo %s failed: %s", url, e)
            continue
        if 8 <= bin(int(h, 16)).count("1") <= 56 and h not in out:
            out.append(h)
    return out


def upload_hero(f: PoliteFetcher, run, source_id: str, photo_urls: list[str]) -> bool:
    """Best effort: a missing hero never fails the listing. The Worker refuses
    logos / stock photos (422 generic); the next photo is tried."""
    for url in photo_urls[:6]:
        try:
            webp, phash = make_hero(f.get_bytes(url))
            res = run.upload_hero(source_id, webp, phash)
            if res.get("phash_match"):
                log.info("hero %s: %s", source_id, res["phash_match"])
            return True
        except Exception as e:
            if getattr(e, "status", None) == 422:
                log.debug("hero %s: %s is a generic image, trying the next photo", source_id, url)
                continue
            log.warning("hero %s from %s failed: %s", source_id, url, e)
    return False

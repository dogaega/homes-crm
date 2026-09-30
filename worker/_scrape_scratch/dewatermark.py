"""
Remove montecarlo-realestate.com's watermark (fixed horizontally-centered
logo, roughly 35-55% down the image) via LaMa inpainting, and read the
high-res image instead of the 200x200 thumbnail.

Usage as a library: from dewatermark import clean_image_bytes
"""
import io
import numpy as np
from PIL import Image
from simple_lama_inpainting import SimpleLama
import torch

_lama = None

def get_lama():
    global _lama
    if _lama is None:
        _lama = SimpleLama(device=torch.device('mps' if torch.backends.mps.is_available() else 'cpu'))
    return _lama

def clean_image_bytes(img_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    w, h = img.size
    # Watermark text observed at ~25-75% width, ~40-53% height — kept tight
    # around the actual glyphs (not a wide band) so LaMa has more real
    # surrounding context to reconstruct from instead of guessing over a
    # huge flat area, which produced visible smearing at a larger radius.
    x0, x1 = int(w * 0.18), int(w * 0.84)
    y0, y1 = int(h * 0.37), int(h * 0.57)
    pad = 60
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(w, x1 + pad), min(h, y1 + pad)

    mask = np.zeros((cy1 - cy0, cx1 - cx0), np.uint8)
    mask[y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0] = 255

    crop = img.crop((cx0, cy0, cx1, cy1))
    result_crop = get_lama()(crop, Image.fromarray(mask))
    result_crop = result_crop.crop((0, 0, cx1 - cx0, cy1 - cy0))

    out = img.copy()
    out.paste(result_crop, (cx0, cy0))
    buf = io.BytesIO()
    out.save(buf, format='JPEG', quality=90)
    return buf.getvalue()

if __name__ == '__main__':
    import sys
    with open(sys.argv[1], 'rb') as f:
        cleaned = clean_image_bytes(f.read())
    with open(sys.argv[2], 'wb') as f:
        f.write(cleaned)
    print('done')

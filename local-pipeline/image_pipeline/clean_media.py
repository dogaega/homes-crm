"""
Monaco/Riviera Local Pipeline — LaMa Inpainting Driver
=========================================================
Orchestrates local, offline watermark removal:

    property_images (status='downloaded')
        -> load the site-specific mask for the portal this image came from
        -> run LaMa (Large Mask Inpainting) to fill the masked region with
           clean, hallucinated texture (sky/ocean/marble — no blur, no crop)
        -> composite: ONLY the masked pixels are replaced by the model's
           output; everything outside the mask is byte-identical to the
           source, so there is zero risk of smudging areas the mask didn't
           cover
        -> encode as optimized WebP (quality 85)
        -> status -> 'inpainted' (picked up next by optimize_and_upload.py
           for the final resize pass + R2 upload)

Mask convention: `/masks/<site_name>_mask.png`, same aspect/resolution
family as that site's listing photos. White (or opaque) pixels = "remove
this and repaint it"; black (or transparent) pixels = "leave untouched".
Any grayscale/alpha mask works — it is thresholded at 50%.

Requires:
    pip install pillow numpy simple-lama-inpainting

`simple-lama-inpainting` bundles the pretrained big-lama JIT model and runs
entirely offline/local (CPU or CUDA if available) — no external API calls.

Usage:
    python clean_media.py
    python clean_media.py --limit 200
    python clean_media.py --masks-dir /path/to/masks --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, UnidentifiedImageError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("clean_media")

WEBP_QUALITY = 85
MASK_THRESHOLD = 128  # 0-255; pixels above this in the mask are "inpaint this"

_lama_model = None  # lazy singleton, loaded once per process


def get_lama_model():
    """
    Lazily loads the LaMa model exactly once. Importing/loading is deferred
    so --dry-run and unit tests don't pay the model-load cost.
    """
    global _lama_model
    if _lama_model is None:
        try:
            from simple_lama_inpainting import SimpleLama
        except ImportError as exc:
            raise RuntimeError(
                "simple-lama-inpainting is not installed. Run: "
                "pip install simple-lama-inpainting"
            ) from exc
        logger.info("Loading LaMa model (first use only, this may take a moment)...")
        _lama_model = SimpleLama()
        logger.info("LaMa model loaded.")
    return _lama_model


@dataclass(frozen=True)
class ImageJob:
    image_id: int
    child_id: int
    site_name: str
    local_raw_path: Path


# ----------------------------------------------------------------------------
# Mask handling
# ----------------------------------------------------------------------------

def load_mask_for_site(masks_dir: Path, site_name: str, target_size: tuple[int, int]) -> Image.Image:
    """
    Loads /masks/<site_name>_mask.png, resizes it to match the raw photo's
    dimensions, and returns a binary ("L" mode) mask: 255 = inpaint,
    0 = leave alone.
    """
    mask_path = masks_dir / f"{site_name}_mask.png"
    if not mask_path.exists():
        raise FileNotFoundError(
            f"No mask configured for site '{site_name}' (expected {mask_path}). "
            f"Every scraped site must have a mask file before its images can "
            f"be cleaned — add one rather than falling back to a blind guess."
        )

    with Image.open(mask_path) as raw_mask:
        # Use alpha if the mask is a transparency cutout; otherwise treat it
        # as a plain grayscale stencil.
        if raw_mask.mode in ("RGBA", "LA"):
            channel = raw_mask.split()[-1]  # alpha channel
        else:
            channel = raw_mask.convert("L")

        if channel.size != target_size:
            channel = channel.resize(target_size, Image.NEAREST)

        binary = channel.point(lambda p: 255 if p >= MASK_THRESHOLD else 0)
        return binary.convert("L")


def mask_covers_any_pixels(mask: Image.Image) -> bool:
    return np.array(mask).max() > 0


# ----------------------------------------------------------------------------
# Inpainting + compositing
# ----------------------------------------------------------------------------

def inpaint_image(source_path: Path, mask: Image.Image) -> Image.Image:
    """
    Runs LaMa over the full image, then composites: only pixels inside the
    mask are taken from the model's output. Every other pixel is copied
    verbatim from the source, byte-for-byte — this is what guarantees "zero
    smudges" outside the watermark region regardless of what the model does
    at the edges.
    """
    model = get_lama_model()

    with Image.open(source_path) as img:
        source = img.convert("RGB")

    inpainted = model(source, mask)  # simple_lama_inpainting returns a PIL.Image
    if inpainted.size != source.size:
        inpainted = inpainted.resize(source.size, Image.LANCZOS)
    inpainted = inpainted.convert("RGB")

    composite = Image.composite(inpainted, source, mask)
    return composite


def save_webp(img: Image.Image, dest_path: Path) -> int:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_path, format="WEBP", quality=WEBP_QUALITY, method=6, exif=b"")
    return dest_path.stat().st_size


# ----------------------------------------------------------------------------
# Database access
# ----------------------------------------------------------------------------

def fetch_pending_jobs(conn: sqlite3.Connection, limit: int) -> list[ImageJob]:
    cur = conn.execute(
        """
        SELECT
            pi.id AS image_id,
            pi.child_id AS child_id,
            s.name AS site_name,
            pi.local_raw_path AS local_raw_path
        FROM property_images pi
        JOIN property_children pc ON pc.id = pi.child_id
        JOIN sites s ON s.id = pc.site_id
        WHERE pi.status = 'downloaded'
          AND pi.local_raw_path IS NOT NULL
        ORDER BY pi.id ASC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        ImageJob(
            image_id=row["image_id"],
            child_id=row["child_id"],
            site_name=row["site_name"],
            local_raw_path=Path(row["local_raw_path"]),
        )
        for row in cur.fetchall()
    ]


def mark_inpainted(conn: sqlite3.Connection, image_id: int, local_clean_path: str) -> None:
    conn.execute(
        """
        UPDATE property_images
        SET status = 'inpainted',
            local_clean_path = ?,
            error_message = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (local_clean_path, datetime.now(timezone.utc).isoformat(), image_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, image_id: int, error_message: str) -> None:
    conn.execute(
        """
        UPDATE property_images
        SET status = 'failed',
            error_message = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (error_message[:500], datetime.now(timezone.utc).isoformat(), image_id),
    )
    conn.commit()


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def process_batch(db_path: Path, masks_dir: Path, limit: int, dry_run: bool) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    jobs = fetch_pending_jobs(conn, limit)
    if not jobs:
        logger.info("No images pending inpainting.")
        conn.close()
        return

    logger.info("Processing %d image(s)...", len(jobs))
    succeeded, failed, skipped = 0, 0, 0

    for job in jobs:
        if not job.local_raw_path.exists():
            mark_failed(conn, job.image_id, f"Raw file not found: {job.local_raw_path}")
            failed += 1
            continue

        try:
            with Image.open(job.local_raw_path) as probe:
                target_size = probe.size
        except (UnidentifiedImageError, OSError) as exc:
            mark_failed(conn, job.image_id, f"Cannot read raw image: {exc}")
            failed += 1
            continue

        try:
            mask = load_mask_for_site(masks_dir, job.site_name, target_size)
        except FileNotFoundError as exc:
            logger.error(str(exc))
            mark_failed(conn, job.image_id, str(exc))
            failed += 1
            continue

        if not mask_covers_any_pixels(mask):
            logger.warning(
                "Mask for site '%s' is entirely empty — image %d has nothing "
                "to inpaint, passing it through unmodified.",
                job.site_name, job.image_id,
            )
            skipped += 1

        dest_path = job.local_raw_path.with_name(
            job.local_raw_path.stem + ".clean.webp"
        )

        if dry_run:
            logger.info(
                "[DRY RUN] Would inpaint image %d (site=%s) -> %s",
                job.image_id, job.site_name, dest_path,
            )
            succeeded += 1
            continue

        try:
            if mask_covers_any_pixels(mask):
                result_img = inpaint_image(job.local_raw_path, mask)
            else:
                with Image.open(job.local_raw_path) as raw:
                    result_img = raw.convert("RGB")
            file_size = save_webp(result_img, dest_path)
        except Exception as exc:  # noqa: BLE001 - any model/IO failure must not crash the batch
            logger.error("Inpainting failed for image %d: %s", job.image_id, exc)
            mark_failed(conn, job.image_id, f"Inpainting error: {exc}")
            failed += 1
            continue

        mark_inpainted(conn, job.image_id, str(dest_path))
        logger.info(
            "Cleaned image %d (site=%s) -> %s (%.1f KB)",
            job.image_id, job.site_name, dest_path, file_size / 1024,
        )
        succeeded += 1

    conn.close()
    logger.info(
        "Done. Succeeded: %d, Failed: %d, Passed through (empty mask): %d",
        succeeded, failed, skipped,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("STAGING_DB_PATH", "pipeline_staging.db")),
        help="Path to the SQLite staging database.",
    )
    parser.add_argument(
        "--masks-dir",
        type=Path,
        default=Path(os.getenv("MASKS_DIR", "../masks")),
        help="Directory containing <site_name>_mask.png files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of images to process in this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without running the model or writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        logger.error("Staging database not found at %s", args.db)
        return 1
    if not args.masks_dir.exists():
        logger.error("Masks directory not found at %s", args.masks_dir)
        return 1
    process_batch(args.db, args.masks_dir, args.limit, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Monaco/Riviera Local Pipeline — Image Optimizer & R2 Uploader
================================================================
Reads raw (already LaMa-inpainted) images referenced in the SQLite staging
database, converts them to optimized WebP via Pillow, uploads them to the
Cloudflare R2 bucket, and writes back the resulting object key / public URL.

This script does NOT run the LaMa inpainting itself (that's a separate,
GPU-bound step). It picks up rows in `property_images` with
status = 'inpainted' (local_clean_path already points at the LaMa output)
and takes them through: WebP optimization -> R2 upload -> status = 'uploaded'.

Requires:
    pip install pillow boto3

Environment variables (put these in a local .env, never commit them):
    R2_ACCOUNT_ID           Cloudflare account id
    R2_ACCESS_KEY_ID        R2 API token access key
    R2_SECRET_ACCESS_KEY    R2 API token secret key
    R2_BUCKET_NAME           Target bucket, e.g. "monaco-riviera-media"
    R2_PUBLIC_BASE_URL        Public base URL for the bucket
                              (custom domain or r2.dev URL), no trailing slash
    STAGING_DB_PATH            Path to pipeline_staging.db (default: ./pipeline_staging.db)

Usage:
    python optimize_and_upload.py
    python optimize_and_upload.py --limit 200
    python optimize_and_upload.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

WEBP_QUALITY = 85
MAX_DIMENSION_PX = 2400  # long-edge cap; keeps 1500-listing media set under R2 free tier
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("optimize_and_upload")


@dataclass(frozen=True)
class R2Settings:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    public_base_url: str

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_env(cls) -> "R2Settings":
        required = {
            "R2_ACCOUNT_ID": os.getenv("R2_ACCOUNT_ID"),
            "R2_ACCESS_KEY_ID": os.getenv("R2_ACCESS_KEY_ID"),
            "R2_SECRET_ACCESS_KEY": os.getenv("R2_SECRET_ACCESS_KEY"),
            "R2_BUCKET_NAME": os.getenv("R2_BUCKET_NAME"),
            "R2_PUBLIC_BASE_URL": os.getenv("R2_PUBLIC_BASE_URL"),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        return cls(
            account_id=required["R2_ACCOUNT_ID"],
            access_key_id=required["R2_ACCESS_KEY_ID"],
            secret_access_key=required["R2_SECRET_ACCESS_KEY"],
            bucket_name=required["R2_BUCKET_NAME"],
            public_base_url=required["R2_PUBLIC_BASE_URL"].rstrip("/"),
        )


def build_r2_client(settings: R2Settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 0}),
        region_name="auto",
    )


# ----------------------------------------------------------------------------
# Image optimization
# ----------------------------------------------------------------------------

def optimize_to_webp(source_path: Path, dest_path: Path) -> tuple[int, int, int]:
    """
    Opens `source_path` (the LaMa-inpainted image), downsamples if needed,
    strips metadata, and writes an optimized WebP to `dest_path`.

    Returns (width, height, file_size_bytes).
    """
    with Image.open(source_path) as img:
        img = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img

        width, height = img.size
        longest_edge = max(width, height)
        if longest_edge > MAX_DIMENSION_PX:
            scale = MAX_DIMENSION_PX / longest_edge
            new_size = (round(width * scale), round(height * scale))
            img = img.resize(new_size, Image.LANCZOS)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(
            dest_path,
            format="WEBP",
            quality=WEBP_QUALITY,
            method=6,       # slower but smaller output, fine for an offline batch job
            exif=b"",       # strip EXIF (GPS, camera/agency metadata)
        )

    file_size = dest_path.stat().st_size
    final_width, final_height = img.size
    return final_width, final_height, file_size


# ----------------------------------------------------------------------------
# R2 upload
# ----------------------------------------------------------------------------

def build_object_key(child_id: int, image_id: int, dest_path: Path) -> str:
    # e.g. properties/child-482/img-1193.webp — flat, predictable, no collisions
    return f"properties/child-{child_id}/img-{image_id}{dest_path.suffix}"


def upload_to_r2(r2_client, settings: R2Settings, local_path: Path, object_key: str) -> None:
    last_error: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r2_client.upload_file(
                Filename=str(local_path),
                Bucket=settings.bucket_name,
                Key=object_key,
                ExtraArgs={
                    "ContentType": "image/webp",
                    "CacheControl": "public, max-age=31536000, immutable",
                },
            )
            return
        except ClientError as exc:
            last_error = exc
            logger.warning(
                "R2 upload attempt %d/%d failed for %s: %s",
                attempt, RETRY_ATTEMPTS, object_key, exc,
            )
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"Failed to upload {object_key} to R2") from last_error


# ----------------------------------------------------------------------------
# Database access
# ----------------------------------------------------------------------------

def fetch_pending_images(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT id, child_id, local_clean_path
        FROM property_images
        WHERE status = 'inpainted'
          AND local_clean_path IS NOT NULL
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    return cur.fetchall()


def mark_uploaded(
    conn: sqlite3.Connection,
    image_id: int,
    r2_object_key: str,
    r2_public_url: str,
    width: int,
    height: int,
    file_size_bytes: int,
) -> None:
    conn.execute(
        """
        UPDATE property_images
        SET status = 'uploaded',
            r2_object_key = ?,
            r2_public_url = ?,
            width_px = ?,
            height_px = ?,
            file_size_bytes = ?,
            error_message = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (
            r2_object_key,
            r2_public_url,
            width,
            height,
            file_size_bytes,
            datetime.now(timezone.utc).isoformat(),
            image_id,
        ),
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

def process_batch(db_path: Path, limit: int, dry_run: bool) -> None:
    settings = R2Settings.from_env()
    r2_client = None if dry_run else build_r2_client(settings)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = fetch_pending_images(conn, limit)
    if not rows:
        logger.info("No images pending optimization/upload.")
        return

    logger.info("Processing %d image(s)...", len(rows))
    succeeded, failed = 0, 0

    for row in rows:
        image_id = row["id"]
        child_id = row["child_id"]
        source_path = Path(row["local_clean_path"])

        if not source_path.exists():
            mark_failed(conn, image_id, f"Source file not found: {source_path}")
            failed += 1
            continue

        webp_path = source_path.with_suffix(".optimized.webp")

        try:
            width, height, file_size = optimize_to_webp(source_path, webp_path)
        except (UnidentifiedImageError, OSError) as exc:
            logger.error("Optimization failed for image %d: %s", image_id, exc)
            mark_failed(conn, image_id, f"Optimization error: {exc}")
            failed += 1
            continue

        object_key = build_object_key(child_id, image_id, webp_path)
        public_url = f"{settings.public_base_url}/{object_key}"

        if dry_run:
            logger.info(
                "[DRY RUN] Would upload image %d -> %s (%dx%d, %.1f KB)",
                image_id, object_key, width, height, file_size / 1024,
            )
            succeeded += 1
            continue

        try:
            upload_to_r2(r2_client, settings, webp_path, object_key)
        except RuntimeError as exc:
            logger.error("Upload failed for image %d: %s", image_id, exc)
            mark_failed(conn, image_id, str(exc))
            failed += 1
            continue

        mark_uploaded(conn, image_id, object_key, public_url, width, height, file_size)
        logger.info(
            "Uploaded image %d -> %s (%dx%d, %.1f KB)",
            image_id, object_key, width, height, file_size / 1024,
        )
        succeeded += 1

    conn.close()
    logger.info("Done. Succeeded: %d, Failed: %d", succeeded, failed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("STAGING_DB_PATH", "pipeline_staging.db")),
        help="Path to the SQLite staging database.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of images to process in this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Optimize images locally but skip the R2 upload and DB write.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        logger.error("Staging database not found at %s", args.db)
        return 1
    try:
        process_batch(args.db, args.limit, args.dry_run)
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

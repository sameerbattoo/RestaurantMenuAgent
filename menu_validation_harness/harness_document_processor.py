#!/usr/bin/env python3
"""Self-contained document helpers for the menu validation harness.

This is a trimmed, dependency-free copy of the extraction helpers from the
production agent's document_processor.py. It contains ONLY the pieces the harness
uses — PDF text extraction, garbled-text detection, HEIC/image preparation, and
JSON parsing with repair — so the menu_validation_harness folder runs standalone
with no imports from the agent package (no strands, utils, menu_tools, s3_storage,
or metrics).

External runtime dependencies (pip): boto3, pillow (PIL), pypdf, and optionally
pillow-heif for HEIC support. The harness's own Bedrock calls live in
test_pipeline.py; this module does no LLM calls.

Kept in sync with agent/document_processor.py — the extraction/parse logic here is
copied verbatim so harness results match the production pipeline.
"""

import json
import logging
import os
import re
import tempfile

from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Register HEIF/HEIC support (optional — iPhone photos)
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    logger.info("pillow-heif not available — HEIC support disabled")

# ─── Supported formats (mirrors document_processor.py) ────────────────────────
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".webp"}
HEIC_EXTENSIONS = {".heic", ".heif"}
MAX_IMAGE_DIMENSION = 2048


# ─── PDF Text Extraction ──────────────────────────────────────────────────────

def _extract_pdf_pages(file_path: str) -> list[str] | None:
    """Extract text from each PDF page using PyPDF.

    Returns a list of per-page text strings, or None if the PDF is scanned/garbled.
    Each page's text is validated independently — pages with insufficient text are skipped.
    """
    try:
        reader = PdfReader(file_path)
        pages = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text and len(page_text.strip()) > 20:
                pages.append(page_text.strip())

        if not pages:
            return None  # No extractable text — likely scanned

        # Check if combined text is garbled
        combined = "\n".join(pages)
        if len(combined) < 50:
            return None
        if _is_garbled(combined):
            return None

        return pages
    except Exception as e:
        logger.warning("PDF text extraction failed: %s", e)
        return None


def _extract_pdf_text(file_path: str) -> str | None:
    """Extract text from PDF using PyPDF. Returns None if text is insufficient or garbled.

    Legacy single-string interface — page-delimited.
    """
    pages = _extract_pdf_pages(file_path)
    if not pages:
        return None
    return "\n\n".join(f"--- Page {i + 1} ---\n{text}" for i, text in enumerate(pages))


def _is_garbled(text: str) -> bool:
    """Detect garbled text from custom font encodings."""
    if len(text) < 100:
        return False
    # Check for glyph ID patterns
    if len(re.findall(r"/gid\d+", text)) > 20:
        return True
    # Check readable character ratio
    readable = sum(1 for c in text if c.isalnum() or c in " .,;:!?$()-/\n")
    return (readable / len(text)) < 0.40


# ─── Image Processing ─────────────────────────────────────────────────────────

def _convert_to_jpeg(file_path: str) -> str:
    """Convert HEIC/HEIF to JPEG, resizing if needed. Returns a temp file path."""
    img = Image.open(file_path)
    if max(img.size) > MAX_IMAGE_DIMENSION:
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    img.convert("RGB").save(tmp.name, "JPEG", quality=85)
    return tmp.name


def _resize_if_needed(file_path: str) -> str:
    """Resize image if it exceeds MAX_IMAGE_DIMENSION. Returns original path if no resize needed."""
    try:
        img = Image.open(file_path)
        if max(img.size) <= MAX_IMAGE_DIMENSION:
            return file_path

        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
        ext = os.path.splitext(file_path)[1].lower()
        suffix = ext if ext in (".jpg", ".jpeg", ".png") else ".jpg"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.close()

        if suffix == ".png":
            img.save(tmp.name, "PNG")
        else:
            img.convert("RGB").save(tmp.name, "JPEG", quality=85)
        return tmp.name
    except Exception:
        return file_path


def _safe_remove(path: str):
    """Remove a file, ignoring errors."""
    try:
        os.remove(path)
    except OSError:
        pass


# ─── JSON Extraction with Repair ──────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Extract JSON from model response with repair for common malformations."""
    if not text:
        return None

    stripped = text.strip()

    # Direct parse
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strip markdown fences
    if "```" in stripped:
        lines = stripped.split("\n")
        json_lines = [l for l in lines if not l.strip().startswith("```")]
        try:
            return json.loads("\n".join(json_lines))
        except (json.JSONDecodeError, TypeError):
            pass

    # Find first { to last }
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last > first:
        candidate = stripped[first:last + 1]
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            # Repair: fix missing opening quotes before keys
            fixed = re.sub(r'(\s+)(\w+)":', r'\1"\2":', candidate)
            try:
                return json.loads(fixed)
            except (json.JSONDecodeError, TypeError):
                pass

    return None

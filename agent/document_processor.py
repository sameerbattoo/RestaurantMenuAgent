#!/usr/bin/env python3
"""Document processor — extracts, validates, and persists restaurant menu data from PDFs and images.

Processing pipeline:
- Text-based PDFs: PyPDF extraction → Haiku (Converse API) for JSON structuring.
- Scanned PDFs / Images: Bedrock Converse API with Sonnet vision for extraction.
- HEIC/HEIF: converted to JPEG before vision processing.
- Images > 2048px: resized with Lanczos before sending to Bedrock.

Validation:
- Rejects files with zero extractable items.
- Rejects files where > MAX_NO_PRICE_PERCENTAGE of items lack pricing.
- Filters out individual no-price items before saving (with user warning).

Conflict handling:
- Fuzzy restaurant name matching (SequenceMatcher >= 0.8 threshold).
- Recommends overwrite (>50% item overlap) or merge (new items found).
- Caches extraction results in-memory to avoid re-processing on confirmation.
"""

import json
import logging
import os
import re
import tempfile
import threading

import boto3
from PIL import Image
from pypdf import PdfReader
from strands import tool

from utils import timed_tool

logger = logging.getLogger(__name__)

# Register HEIF/HEIC support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    logger.info("pillow-heif not available — HEIC support disabled")

# Cache for extracted menu data (avoids re-processing on overwrite/merge confirmation)
_extraction_cache: dict[str, dict] = {}  # file_path -> {menu_data, existing_entry}
_cache_lock = threading.Lock()
_CACHE_MAX_SIZE = 20  # Evict oldest entries beyond this limit

# Supported formats
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".webp"}
HEIC_EXTENSIONS = {".heic", ".heif"}
MAX_IMAGE_DIMENSION = 2048

# Price validation: reject files where more than this % of items have no price
MAX_NO_PRICE_PERCENTAGE = int(os.environ.get("MAX_NO_PRICE_PERCENTAGE", "50"))

# Token limits for Bedrock model output
MAX_OUTPUT_TOKENS_ANTHROPIC = int(os.environ.get("MAX_OUTPUT_TOKENS_ANTHROPIC", "16384"))
MAX_OUTPUT_TOKENS_HAIKU = 8192
MAX_OUTPUT_TOKENS_NOVA = 10000

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

EXTRACTION_PROMPT = """\
You are a menu data extraction system. Look at the attached restaurant menu image or document, extract ALL visible items, and return ONLY valid JSON.

IMPORTANT: Return raw JSON only. No markdown, no code fences, no explanation. Every key must be in double quotes.

EXACT OUTPUT FORMAT (follow this precisely):
{
  "restaurant_name": "The Restaurant Name",
  "categories": [
    {
      "name": "Appetizers",
      "items": [
        {
          "name": "Spring Rolls",
          "price": "8.99",
          "description": "Crispy vegetable rolls served with sweet chili sauce",
          "dietary_info": ["vegetarian"]
        },
        {
          "name": "Chicken Wings",
          "price": "12.99",
          "description": "Spicy buffalo wings with ranch dip",
          "dietary_info": []
        }
      ]
    },
    {
      "name": "Main Course",
      "items": [
        {
          "name": "Grilled Salmon",
          "price": "24.99",
          "description": "Atlantic salmon with lemon butter sauce",
          "dietary_info": ["gluten-free"]
        }
      ]
    }
  ],
  "metadata": {
    "total_items": 3,
    "price_range": {"min": 8.99, "max": 24.99},
    "dietary_options": ["vegetarian", "gluten-free"]
  }
}

RULES:
1. Every item MUST have all 4 fields: "name", "price", "description", "dietary_info"
2. "price" = string without $ (e.g., "12.99"). Use null if not visible.
3. "description" = string. Use "" if none available.
4. "dietary_info" = array of strings. Use [] if none mentioned.
5. "metadata.total_items" = exact count of all items across all categories.
6. "metadata.price_range" = actual min/max from extracted prices.
7. "metadata.dietary_options" = unique list of all dietary tags found.
8. Capture EVERY item on the menu — do not skip any.
9. Group items by their category headers as shown on the menu.
10. Return ONLY the JSON object. No other text before or after.
"""


@tool
def process_document(file_path: str, action: str = "auto") -> str:
    """
    Process a restaurant menu document — extracts, structures, validates pricing, and handles conflicts.

    Pipeline:
    1. Extraction: PDF text via PyPDF → Haiku structuring, or Bedrock vision (Sonnet) for images/scanned PDFs.
    2. JSON parsing with repair (handles markdown fences, truncation, malformed keys).
    3. Validation:
       - Rejects files with zero items extracted.
       - Rejects files where > MAX_NO_PRICE_PERCENTAGE of items lack pricing.
       - Filters out individual items without prices (warns user about removed items).
    4. Conflict detection (fuzzy restaurant name matching, SequenceMatcher >= 0.8):
       - New restaurant → saves automatically to DynamoDB + S3.
       - Existing restaurant → returns recommendation (overwrite if >50% item overlap, merge otherwise).
    5. Caches extracted data in-memory for the second call (avoids re-processing on confirmation).

    On first call (action="auto"):
    - Extracts, validates, and checks for conflicts.
    - If new: saves automatically, returns summary.
    - If exists: returns conflict report with recommendation + instructions.

    On second call (action="overwrite" or action="merge"):
    - Uses cached extraction data (no re-processing).
    - "overwrite": replaces the existing DynamoDB entry entirely.
    - "merge": adds new categories/items into the existing entry (no duplicates).

    Args:
        file_path: Path to the document file (PDF, JPG, PNG, HEIC, TIFF, WEBP, BMP).
        action: "auto" (default — extract + detect conflicts),
                "overwrite" (replace existing entry),
                "merge" (combine into existing entry).

    Returns:
        JSON string with one of:
        - Success: status, restaurant_name, categories, total_items, price_range, time_seconds.
        - Conflict: status="conflict_detected", recommendation, overlap stats, instructions.
        - Error: error message with context (file_name, time_seconds).
        - Warning (on success): items_removed_no_price list if some items were filtered.
    """
    import time as _time
    from menu_tools import _get_menu, _put_menu, _compute_stats, _from_dynamodb
    from s3_storage import _ORIGINALS_PREFIX

    if not os.path.exists(file_path):
        # Check if we have cached data (second call for overwrite/merge)
        with _cache_lock:
            if file_path not in _extraction_cache:
                return json.dumps({"error": f"File not found: {file_path}"})

    ext = os.path.splitext(file_path)[1].lower()
    file_name = os.path.basename(file_path)
    start = _time.time()

    # Check cache first (second call with action="overwrite"/"merge" skips extraction)
    with _cache_lock:
        cached_entry = _extraction_cache.get(file_path) if action in ("overwrite", "merge") else None
    items_without_price = []  # Default for cached path
    if cached_entry is not None:
        menu_data = cached_entry["menu_data"]
        cached_existing = cached_entry.get("existing_entry")
    else:
        # Step 1: Extract and structure
        try:
            if ext == ".pdf":
                result_text = _process_pdf(file_path)
            elif ext in HEIC_EXTENSIONS:
                result_text = _process_heic(file_path)
            elif ext in IMAGE_EXTENSIONS:
                result_text = _process_image(file_path)
            else:
                supported = ", ".join(sorted({".pdf"} | IMAGE_EXTENSIONS | HEIC_EXTENSIONS))
                return json.dumps({"error": f"Unsupported format: {ext}. Supported: {supported}"})
        except Exception as e:
            logger.error("Document processing failed for %s: %s", file_name, e)
            return json.dumps({"error": f"Processing failed: {e}"})

        # Step 2: Parse JSON (with repair)
        menu_data = _extract_json(result_text)
        if menu_data is None:
            logger.error("JSON extraction failed for %s. Raw (first 500): %s",
                         file_name, (result_text or "")[:500])
            return json.dumps({
                "error": "Could not parse structured data from document.",
                "file_name": file_name,
                "time_seconds": round(_time.time() - start, 2),
            })

        # Step 3: Validate
        categories = menu_data.get("categories", [])
        if not categories:
            return json.dumps({
                "error": "Extraction produced no menu categories.",
                "file_name": file_name,
                "time_seconds": round(_time.time() - start, 2),
            })

        # Check if there are any items at all
        total_item_count = sum(len(cat.get("items", [])) for cat in categories)
        if total_item_count == 0:
            return json.dumps({
                "error": "Extraction produced categories but no menu items. The file may not contain a valid menu.",
                "file_name": file_name,
                "categories_found": [cat.get("name", "") for cat in categories],
                "time_seconds": round(_time.time() - start, 2),
            }, indent=2)

        # Step 3b: Price validation
        all_items = []
        items_without_price = []
        for cat in categories:
            for item in cat.get("items", []):
                all_items.append(item)
                price = item.get("price")
                if not price or str(price).strip() in ("", "null", "None", "0"):
                    items_without_price.append({
                        "name": item.get("name", "Unknown"),
                        "category": cat.get("name", "Unknown"),
                    })

        total_items = len(all_items)
        no_price_count = len(items_without_price)
        no_price_pct = (no_price_count / total_items * 100) if total_items > 0 else 0

        # Reject if too many items have no price
        if no_price_pct > MAX_NO_PRICE_PERCENTAGE:
            return json.dumps({
                "error": "Too many items without pricing — file rejected.",
                "file_name": file_name,
                "total_items": total_items,
                "items_without_price": no_price_count,
                "no_price_percentage": round(no_price_pct, 1),
                "threshold": MAX_NO_PRICE_PERCENTAGE,
                "reason": f"{no_price_count}/{total_items} items ({no_price_pct:.0f}%) have no price. Threshold is {MAX_NO_PRICE_PERCENTAGE}%.",
                "time_seconds": round(_time.time() - start, 2),
            }, indent=2)

        # Remove items without price from the data before saving
        if items_without_price:
            for cat in categories:
                cat["items"] = [
                    item for item in cat.get("items", [])
                    if item.get("price") and str(item["price"]).strip() not in ("", "null", "None", "0")
                ]
            # Remove empty categories after filtering
            menu_data["categories"] = [cat for cat in categories if cat.get("items")]
            categories = menu_data["categories"]

            # If nothing remains after filtering, reject
            if not categories:
                return json.dumps({
                    "error": "All menu items lack pricing — file rejected.",
                    "file_name": file_name,
                    "total_items_found": total_items,
                    "items_without_price": no_price_count,
                    "reason": "After removing items without prices, no items remain.",
                    "time_seconds": round(_time.time() - start, 2),
                }, indent=2)

        # Cache for potential second call
        with _cache_lock:
            _extraction_cache[file_path] = {"menu_data": menu_data, "existing_entry": None}
            # Evict oldest if cache exceeds limit
            if len(_extraction_cache) > _CACHE_MAX_SIZE:
                oldest_key = next(iter(_extraction_cache))
                _extraction_cache.pop(oldest_key, None)

    new_restaurant = menu_data.get("restaurant_name", "").strip()
    new_items = _get_all_item_names(menu_data)
    s3_key = f"{_ORIGINALS_PREFIX}/{file_name}"
    stats = _compute_stats(menu_data)

    # Build warning about removed items (if any)
    price_warning = None
    if items_without_price:
        price_warning = {
            "items_removed_no_price": len(items_without_price),
            "removed_items": items_without_price[:10],  # Show first 10
        }

    # Step 4: Check for existing restaurant in DB (only on "auto" — skip on explicit action)
    if action == "auto":
        existing_entry = _find_existing_restaurant(new_restaurant)
    else:
        # On overwrite/merge, use cached existing_entry from first call
        existing_entry = cached_existing if cached_entry else None

    if existing_entry is None or action == "overwrite":
        # No conflict OR user chose overwrite — save directly
        _put_menu(file_name, menu_data, s3_key=s3_key)
        with _cache_lock:
            _extraction_cache.pop(file_path, None)  # Clean cache
        elapsed_total = round(_time.time() - start, 2)

        status = "Menu processed and saved"
        if action == "overwrite" and existing_entry:
            status = "Menu overwritten (replaced existing)"

        result = {
            "status": status,
            "file_name": file_name,
            "restaurant_name": new_restaurant or "Unknown",
            "total_categories": stats["total_categories"],
            "categories": stats["categories"],
            "total_items": stats["total_items"],
            "price_range": stats["price_range"],
            "time_seconds": elapsed_total,
        }
        if price_warning:
            result["warning"] = price_warning

        return json.dumps(result, indent=2)

    elif action == "merge":
        # User chose merge — combine new items into existing entry
        existing_file = existing_entry["file_name"]
        existing_menu = _get_menu(existing_file)

        if existing_menu:
            merge_result = _merge_into_existing(existing_menu, menu_data)

            # Track merged files (before the single save)
            merged_from = existing_menu.get("metadata", {}).get("merged_from", [])
            if existing_file not in merged_from:
                merged_from.append(existing_file)
            if file_name not in merged_from:
                merged_from.append(file_name)
            existing_menu.setdefault("metadata", {})["merged_from"] = merged_from

            # Preserve S3 keys from both original files
            s3_keys = existing_menu.get("metadata", {}).get("s3_keys", [])
            if existing_menu.get("s3_key") and existing_menu["s3_key"] not in s3_keys:
                s3_keys.append(existing_menu["s3_key"])
            new_s3_key = f"{_ORIGINALS_PREFIX}/{file_name}"
            if new_s3_key not in s3_keys:
                s3_keys.append(new_s3_key)
            existing_menu.setdefault("metadata", {})["s3_keys"] = s3_keys

            # Single save
            _put_menu(existing_file, existing_menu, s3_key=existing_menu.get("s3_key"))

            with _cache_lock:
                _extraction_cache.pop(file_path, None)
            elapsed_total = round(_time.time() - start, 2)

            return json.dumps({
                "status": "Menu merged into existing restaurant",
                "target_file": existing_file,
                "merged_from": file_name,
                "new_categories_added": merge_result["new_categories"],
                "new_items_added": merge_result["new_items"],
                "total_items_now": sum(len(c.get("items", [])) for c in existing_menu.get("categories", [])),
                "time_seconds": elapsed_total,
            }, indent=2)

    # action == "auto" and existing entry found — analyze conflict
    existing_file = existing_entry["file_name"]
    existing_items = existing_entry["items"]
    overlap = len(new_items & existing_items)
    total_new = len(new_items - existing_items)

    # Determine recommendation
    if len(new_items) > 0 and overlap / len(new_items) > 0.5:
        recommendation = "overwrite"
        reason = f"{overlap}/{len(new_items)} items already exist — this looks like a re-upload of the same menu"
    else:
        recommendation = "merge"
        reason = f"{total_new} new items found — this looks like an additional page/section"

    # Update cache with existing_entry for the second call
    with _cache_lock:
        _extraction_cache[file_path] = {"menu_data": menu_data, "existing_entry": existing_entry}

    elapsed_total = round(_time.time() - start, 2)

    return json.dumps({
        "status": "conflict_detected",
        "file_name": file_name,
        "restaurant_name": new_restaurant,
        "new_items_count": len(new_items),
        "existing_file": existing_file,
        "existing_items_count": len(existing_items),
        "overlap_count": overlap,
        "unique_new_items": total_new,
        "recommendation": recommendation,
        "reason": reason,
        "instructions": f"Call process_document again with action='{recommendation}' to proceed, or action='overwrite' to replace entirely.",
        "new_menu_summary": {
            "total_categories": stats["total_categories"],
            "categories": stats["categories"],
            "total_items": stats["total_items"],
            "price_range": stats["price_range"],
        },
        "time_seconds": elapsed_total,
    }, indent=2)


# ─── Format-specific processors ───────────────────────────────────────────────

def _process_pdf(file_path: str) -> str:
    """Extract text with PyPDF (fast), structure with Haiku (fast). Fall back to vision for scanned PDFs."""
    text = _extract_pdf_text(file_path)
    if text:
        # Text-based PDF: structure with Haiku (much faster than Sonnet for JSON generation)
        return _structure_text_with_bedrock(text)
    # Scanned/image-based PDF: must use vision
    return _call_bedrock_vision(file_path)


def _structure_text_with_bedrock(raw_text: str) -> str:
    """Use Bedrock Converse API to structure raw menu text into JSON (no vision needed).
    
    Uses Haiku for speed — structuring text into JSON doesn't need Sonnet's reasoning.
    """
    from metrics import report_usage
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 3, "mode": "adaptive"}),
    )
    # Use Haiku for structuring — much faster for text→JSON conversion
    model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    prompt = f"""Structure this restaurant menu text into JSON. Return ONLY valid JSON.

Format:
{{"restaurant_name":"...","categories":[{{"name":"...","items":[{{"name":"...","price":"12.99","description":"...","dietary_info":["vegetarian"]}}]}}],"metadata":{{"total_items":N,"price_range":{{"min":N,"max":N}},"dietary_options":["..."]}}}}

Rules: prices as strings without $, null if missing, include ALL items, group by category.

Menu text:
{raw_text}"""

    response = client.converse(
        modelId=model_id,
        messages=[{
            "role": "user",
            "content": [{"text": prompt}],
        }],
        inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS_HAIKU},
    )

    output = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    report_usage(
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
        source="structure_text",
    )

    return output


def _process_heic(file_path: str) -> str:
    """Convert HEIC to JPEG, then process with Bedrock."""
    converted = _convert_to_jpeg(file_path)
    try:
        return _call_bedrock_vision(converted)
    finally:
        if converted != file_path:
            _safe_remove(converted)


def _process_image(file_path: str) -> str:
    """Resize if needed, then process with Bedrock."""
    resized = _resize_if_needed(file_path)
    try:
        return _call_bedrock_vision(resized)
    finally:
        if resized != file_path:
            _safe_remove(resized)


# ─── PDF Text Extraction ──────────────────────────────────────────────────────

def _extract_pdf_text(file_path: str) -> str | None:
    """Extract text from PDF using PyPDF. Returns None if text is insufficient or garbled."""
    try:
        reader = PdfReader(file_path)
        pages = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                pages.append(f"--- Page {i + 1} ---\n{page_text}")

        text = "\n\n".join(pages).strip()

        if len(text) < 50:
            return None  # Too little text — likely scanned

        if _is_garbled(text):
            return None  # Garbled font encoding

        return text
    except Exception as e:
        logger.warning("PDF text extraction failed: %s", e)
        return None


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
    """Convert HEIC/HEIF to JPEG, resizing if needed."""
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


# ─── Bedrock Vision ───────────────────────────────────────────────────────────

def _call_bedrock_vision(file_path: str) -> str:
    """Use Bedrock Converse API for vision — works with all models."""
    from metrics import report_usage
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 3, "mode": "adaptive"}),
    )
    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    ext = os.path.splitext(file_path)[1].lower()
    format_map = {
        ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png",
        ".gif": "gif", ".webp": "webp", ".pdf": "pdf",
    }
    img_format = format_map.get(ext, "jpeg")

    # Build content block for Converse API
    if ext == ".pdf":
        doc_block = {
            "document": {
                "format": "pdf",
                "name": "menu",
                "source": {"bytes": file_bytes},
            }
        }
    else:
        doc_block = {
            "image": {
                "format": img_format,
                "source": {"bytes": file_bytes},
            }
        }

    # Dynamic max_tokens based on file size and model limits
    max_tokens = _estimate_max_tokens(file_bytes, model_id)

    response = client.converse(
        modelId=model_id,
        messages=[{
            "role": "user",
            "content": [doc_block, {"text": EXTRACTION_PROMPT}],
        }],
        inferenceConfig={"maxTokens": max_tokens},
    )

    output = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    report_usage(
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
        source="process_document",
    )
    return output


# ─── Conflict Detection Helpers ───────────────────────────────────────────────

def _get_all_item_names(menu_data: dict) -> set:
    """Get a set of all item names (lowercased) from a menu."""
    names = set()
    for cat in menu_data.get("categories", []):
        for item in cat.get("items", []):
            name = item.get("name", "").strip().lower()
            if name:
                names.add(name)
    return names


def _find_existing_restaurant(restaurant_name: str) -> dict | None:
    """Search DynamoDB for an existing entry with the same restaurant name.
    
    Uses fuzzy matching (SequenceMatcher >= 0.8 threshold) to avoid false positives
    like "Pizza" matching "Pizza Hut".
    
    Returns dict with file_name and items (set of item names), or None.
    """
    if not restaurant_name:
        return None

    from difflib import SequenceMatcher
    import boto3
    from menu_tools import _table, _from_dynamodb

    # Scan for matching restaurant name (case-insensitive)
    response = _table.scan(
        ProjectionExpression="file_name, restaurant_name, categories",
    )
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = _table.scan(
            ProjectionExpression="file_name, restaurant_name, categories",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    target = restaurant_name.lower().strip()
    for item in items:
        item = _from_dynamodb(item)
        existing_name = (item.get("restaurant_name") or "").lower().strip()
        if not existing_name:
            continue
        # Use SequenceMatcher for fuzzy matching with 0.8 threshold
        similarity = SequenceMatcher(None, target, existing_name).ratio()
        if similarity >= 0.8:
            # Found a match
            existing_items = _get_all_item_names(item)
            return {
                "file_name": item["file_name"],
                "restaurant_name": item.get("restaurant_name", ""),
                "items": existing_items,
            }

    return None


def _merge_into_existing(existing_menu: dict, new_menu: dict) -> dict:
    """Merge new menu items into existing menu. Returns stats about what was added."""
    new_categories = 0
    new_items = 0

    existing_cats = {cat["name"].lower(): cat for cat in existing_menu.get("categories", [])}

    for new_cat in new_menu.get("categories", []):
        cat_key = new_cat["name"].lower()

        if cat_key in existing_cats:
            # Category exists — add non-duplicate items
            existing_item_names = {i["name"].lower() for i in existing_cats[cat_key].get("items", [])}
            for item in new_cat.get("items", []):
                if item["name"].lower() not in existing_item_names:
                    existing_cats[cat_key].setdefault("items", []).append(item)
                    new_items += 1
        else:
            # New category
            existing_menu.setdefault("categories", []).append(new_cat)
            new_categories += 1
            new_items += len(new_cat.get("items", []))

    # Update metadata
    total = sum(len(c.get("items", [])) for c in existing_menu.get("categories", []))
    existing_menu.setdefault("metadata", {})["total_items"] = total

    # Recalculate price_range and dietary_options
    prices = []
    dietary_options = set()
    for cat in existing_menu.get("categories", []):
        for item in cat.get("items", []):
            price = item.get("price")
            if price is not None:
                try:
                    prices.append(float(str(price).replace("$", "").replace(",", "")))
                except (ValueError, TypeError):
                    pass
            for tag in item.get("dietary_info", []):
                if tag:
                    dietary_options.add(tag)

    existing_menu["metadata"]["price_range"] = {
        "min": min(prices) if prices else "N/A",
        "max": max(prices) if prices else "N/A",
    }
    existing_menu["metadata"]["dietary_options"] = sorted(dietary_options)

    return {"new_categories": new_categories, "new_items": new_items}


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


# ─── Utilities ────────────────────────────────────────────────────────────────

def _safe_remove(path: str):
    """Remove a file, ignoring errors."""
    try:
        os.remove(path)
    except OSError:
        pass


def _estimate_max_tokens(file_bytes: bytes, model_id: str) -> int:
    """Get max_tokens for a Bedrock vision call based on model limits.
    
    For vision calls, we can't reliably estimate output size from file size
    (a small compressed JPEG can contain a 100+ item menu). Use model max.
    
    Args:
        file_bytes: The file content as bytes (unused, kept for API consistency)
        model_id: The Bedrock model ID
    
    Returns:
        Model's maximum output tokens
    """
    return MAX_OUTPUT_TOKENS_NOVA if "nova" in model_id.lower() else MAX_OUTPUT_TOKENS_ANTHROPIC

"""Standalone document processor for batch processing.

Extracts and structures menu data from PDFs/images using Bedrock or Textract.
Returns structured JSON — does NOT save to DynamoDB (that's the agent's job).

Supports multiple extraction methods:
- LLM Vision (Sonnet/Opus/Nova): Single call does OCR + structuring
- Textract TABLES + Haiku: Textract for OCR with item-price pairing, Haiku for JSON structuring

Usage:
    Called by batch_process_menus.py — not intended for direct use.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid

import boto3
from PIL import Image
from pypdf import PdfReader
from strands import tool

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# THREAD-SAFE USAGE TRACKING
#
# The batch processor runs files in parallel via ThreadPoolExecutor.
# Module-level dicts are NOT safe for per-file tracking across threads.
# We use threading.local() to give each worker its own counters.
# ═══════════════════════════════════════════════════════════════════════════════

# Legacy module-level trackers (kept for backward compatibility with single-threaded use)
_last_usage = {"input_tokens": 0, "output_tokens": 0}
_textract_usage = {"pages": 0}

# Thread-local storage — each worker thread gets independent counters
_thread_local = threading.local()


def _get_thread_usage():
    """Get thread-local usage trackers. Safe for parallel execution."""
    if not hasattr(_thread_local, "last_usage"):
        _thread_local.last_usage = {"input_tokens": 0, "output_tokens": 0}
    if not hasattr(_thread_local, "textract_usage"):
        _thread_local.textract_usage = {"pages": 0}
    return _thread_local.last_usage, _thread_local.textract_usage


def reset_thread_usage():
    """Reset thread-local counters. Call at the start of each file processing."""
    _thread_local.last_usage = {"input_tokens": 0, "output_tokens": 0}
    _thread_local.textract_usage = {"pages": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS & CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Register HEIF/HEIC support (iPhone photos)
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

# Supported file formats
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".webp"}
HEIC_EXTENSIONS = {".heic", ".heif"}

# Image processing limits
MAX_IMAGE_DIMENSION = 2048  # Resize images larger than this (pixels)

# Bedrock model output token limits
MAX_OUTPUT_TOKENS_ANTHROPIC = 16384
MAX_OUTPUT_TOKENS_NOVA = 10000

# Textract configuration
TEXTRACT_PRICE_PER_PAGE = 0.015  # TABLES feature cost per page
TEXTRACT_POLL_INTERVAL = 5       # Seconds between async job status checks
TEXTRACT_POLL_TIMEOUT = 120      # Max seconds to wait for async job

# Media type mapping for Bedrock Converse API
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


# ═══════════════════════════════════════════════════════════════════════════════
# LLM VISION PIPELINE
#
# Used by: --model sonnet, --model opus, --model nova-pro, --model haiku
# Flow: File → Bedrock Vision API (single call does OCR + JSON structuring)
# For PDFs with extractable text: PyPDF text extraction → Haiku structuring
# ═══════════════════════════════════════════════════════════════════════════════

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
        }
      ]
    }
  ],
  "metadata": {
    "total_items": 1,
    "price_range": {"min": 8.99, "max": 8.99},
    "dietary_options": ["vegetarian"]
  }
}

RULES:
1. Every item MUST have all 4 fields: "name", "price", "description", "dietary_info"
2. "price" = string without $ (e.g., "12.99"). Use null if price is not visible — but STILL INCLUDE THE ITEM.
3. "description" = string. Use "" if none available.
4. "dietary_info" = array of strings. Use [] if none mentioned.
5. "metadata.total_items" = exact count of all items across all categories.
6. "metadata.price_range" = actual min/max from extracted prices (ignore null prices).
7. "metadata.dietary_options" = unique list of all dietary tags found.
8. Capture EVERY item on the menu — do not skip any, even if price is missing.
9. Group items by their category headers as shown on the menu.
10. Return ONLY the JSON object. No other text before or after.
"""


@tool
def process_document(file_path: str) -> str:
    """Process a restaurant menu document via LLM vision. Entry point for Strands agent.

    Routes to the appropriate processor based on file type:
    - PDF with extractable text → PyPDF + Haiku (cheap, fast)
    - PDF without text (scanned) → Bedrock Vision (OCR + structuring)
    - Images → Bedrock Vision
    - HEIC → Convert to JPEG → Bedrock Vision
    """
    if not os.path.exists(file_path):
        return json.dumps({"error": f"File not found: {file_path}"})

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            return _process_pdf(file_path)
        elif ext in HEIC_EXTENSIONS:
            return _process_heic(file_path)
        elif ext in IMAGE_EXTENSIONS:
            return _process_image(file_path)
        else:
            return json.dumps({"error": f"Unsupported format: {ext}"})
    except Exception as e:
        return json.dumps({"error": f"Processing failed: {e}"})


def _process_pdf(file_path: str) -> str:
    """Process PDF: try text extraction first (cheap), fall back to vision (expensive)."""
    text = _extract_pdf_text(file_path)
    if text:
        return _structure_text_with_bedrock(text)
    return _call_bedrock_vision(file_path)


def _process_heic(file_path: str) -> str:
    """Process HEIC: convert to JPEG then use vision."""
    converted = _convert_to_jpeg(file_path)
    try:
        return _call_bedrock_vision(converted)
    finally:
        if converted != file_path:
            _safe_remove(converted)


def _process_image(file_path: str) -> str:
    """Process image: resize if too large, then use vision."""
    resized = _resize_if_needed(file_path)
    try:
        return _call_bedrock_vision(resized)
    finally:
        if resized != file_path:
            _safe_remove(resized)


# ═══════════════════════════════════════════════════════════════════════════════
# PDF TEXT EXTRACTION (used by LLM pipeline for PDFs with embedded text)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_pdf_text(file_path: str) -> str | None:
    """Extract text from PDF using PyPDF. Returns None if text is garbled or too short."""
    try:
        reader = PdfReader(file_path)
        pages = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                pages.append(f"--- Page {i + 1} ---\n{page_text}")
        text = "\n\n".join(pages).strip()

        # Reject if too short (likely a scanned PDF with no embedded text)
        if len(text) < 50:
            return None
        # Reject if garbled (font encoding issues)
        if _is_garbled(text):
            return None
        return text
    except Exception:
        return None


def _is_garbled(text: str) -> bool:
    """Detect garbled text from PDFs with broken font encoding."""
    if len(text) < 100:
        return False
    # CID-keyed fonts produce /gidNNN patterns
    if len(re.findall(r"/gid\d+", text)) > 20:
        return True
    # Less than 40% readable characters = garbled
    readable = sum(1 for c in text if c.isalnum() or c in " .,;:!?$()-/\n")
    return (readable / len(text)) < 0.40


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE CONVERSION UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _convert_to_jpeg(file_path: str) -> str:
    """Convert HEIC/HEIF to JPEG. Also resizes if larger than MAX_IMAGE_DIMENSION."""
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
    """Remove a temp file, ignoring errors."""
    try:
        os.remove(path)
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# BEDROCK API CALLS (shared by LLM vision pipeline and Textract structuring)
# ═══════════════════════════════════════════════════════════════════════════════

def _structure_text_with_bedrock(raw_text: str) -> str:
    """Send extracted text to Haiku for JSON structuring.
    
    Used by both:
    - LLM pipeline: PyPDF text → Haiku → JSON
    - Textract pipeline: Textract table rows → Haiku → JSON
    """
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 2}),
    )
    model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    prompt = f"""Structure this restaurant menu text into JSON. Return ONLY valid JSON.

Format:
{{"restaurant_name":"...","categories":[{{"name":"...","items":[{{"name":"...","price":"12.99","description":"...","dietary_info":["vegetarian"]}}]}}],"metadata":{{"total_items":N,"price_range":{{"min":N,"max":N}},"dietary_options":["..."]}}}}

Rules: prices as strings without $, null if missing (but STILL INCLUDE the item), include ALL items even without prices, group by category.

Menu text:
{raw_text}"""

    # Scale max_tokens: large menus produce more output JSON
    estimated_items = raw_text.count('\n') // 2
    max_tokens = min(max(8192, estimated_items * 60), 32000)

    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }),
    )

    body = json.loads(response["body"].read().decode("utf-8"))
    usage = body.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    # Update both module-level (legacy) and thread-local (parallel-safe) trackers
    _last_usage["input_tokens"] += input_tokens
    _last_usage["output_tokens"] += output_tokens
    thread_usage, _ = _get_thread_usage()
    thread_usage["input_tokens"] += input_tokens
    thread_usage["output_tokens"] += output_tokens

    return body["content"][0]["text"]


def _call_bedrock_vision(file_path: str) -> str:
    """Send file to Bedrock Vision for OCR + structuring in a single call.
    
    Uses the Converse API which works with all Bedrock models (Anthropic, Nova, etc.)
    without format-specific branching.
    """
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        config=Config(read_timeout=300, connect_timeout=10),
    )
    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    ext = os.path.splitext(file_path)[1].lower()

    # Build the appropriate content block (document vs image)
    format_map = {
        ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png",
        ".gif": "gif", ".webp": "webp", ".pdf": "pdf",
    }
    img_format = format_map.get(ext, "jpeg")

    if ext == ".pdf":
        doc_block = {"document": {"format": "pdf", "name": "menu", "source": {"bytes": file_bytes}}}
    else:
        doc_block = {"image": {"format": img_format, "source": {"bytes": file_bytes}}}

    # Use model-appropriate max_tokens to avoid truncation
    max_tokens = MAX_OUTPUT_TOKENS_NOVA if "nova" in model_id.lower() else MAX_OUTPUT_TOKENS_ANTHROPIC

    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [doc_block, {"text": EXTRACTION_PROMPT}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )

    output = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    _last_usage["input_tokens"] += usage.get("inputTokens", 0)
    _last_usage["output_tokens"] += usage.get("outputTokens", 0)
    return output


# ═══════════════════════════════════════════════════════════════════════════════
# TEXTRACT PIPELINE
#
# Used by: --model textract
# Flow: File → Textract TABLES → Structured rows → Haiku → JSON
#
# Why TABLES and not LAYOUT?
# ─────────────────────────────────────────────────────────────────────────────
# Benchmarked all Textract FeatureTypes on 12 restaurant menus:
#   - TABLES only:        607 items ← WINNER (matches Sonnet's 608)
#   - FORMS only:         600 items
#   - TABLES + FORMS:     594 items (combined output confuses Haiku)
#   - LAYOUT only:        320 items (prices disconnected from item names)
#
# TABLES wins because menus visually align items (left) with prices (right).
# Textract detects this as table structure — even without grid borders —
# returning Row/Col cells where Col 1 = item and Col 2 = price.
#
# LINE blocks (raw text) are returned FREE with any AnalyzeDocument call,
# providing section headers and restaurant name without extra cost.
#
# See: docs/textract-feature-comparison-results.md
# ═══════════════════════════════════════════════════════════════════════════════

def _get_page_count(file_path: str) -> int:
    """Determine page count for sync/async routing.
    
    - Images: always 1 page → sync API
    - PDFs: PyPDF page count → 1 = sync, 2+ = async
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        try:
            return len(PdfReader(file_path).pages)
        except Exception:
            return 1
    return 1


def process_with_textract(file_path: str) -> str:
    """Process a menu file using Textract TABLES + Haiku structuring.
    
    Pipeline:
    1. Convert HEIC to JPEG if needed (Textract doesn't support HEIC)
    2. Detect page count → route to sync (1 page) or async (2+ pages)
    3. Call Textract with FeatureTypes=["TABLES"]
    4. Extract table rows (item | price) + non-table lines (headers)
    5. Send formatted text to Haiku for JSON structuring
    
    Benchmarked: 607 items extracted from 12 menus (matches Sonnet's 608).
    Cost: $0.015/page (Textract) + ~$0.005/file (Haiku) ≈ 40% cheaper than Sonnet.
    """
    ext = os.path.splitext(file_path)[1].lower()
    converted_path = None
    _, textract_thread_usage = _get_thread_usage()

    try:
        # Step 1: Convert HEIC to JPEG (Textract doesn't support HEIC/HEIF)
        if ext in HEIC_EXTENSIONS:
            converted_path = _convert_to_jpeg(file_path)
            file_path = converted_path

        # Step 2: Route sync vs async based on page count
        page_count = _get_page_count(file_path)
        _textract_usage["pages"] += page_count
        textract_thread_usage["pages"] += page_count

        if page_count == 1:
            blocks = _textract_sync(file_path)
        else:
            blocks = _textract_async(file_path)

        # Step 3: Extract structured text from table blocks
        text = _extract_textract_tables(blocks)

        if not text or len(text.strip()) < 20:
            return json.dumps({"error": "Textract returned insufficient text"})

        # Step 4: Structure with Haiku
        return _structure_text_with_bedrock(text)

    finally:
        if converted_path and converted_path != file_path:
            _safe_remove(converted_path)


def _textract_sync(file_path: str) -> list:
    """Single-page Textract call (sync). Sends raw bytes — no S3 upload needed.
    
    Used for: all images, single-page PDFs.
    Cost: $0.015/page (TABLES feature).
    """
    client = boto3.client("textract", region_name=os.environ.get("AWS_REGION", "us-west-2"))

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    response = client.analyze_document(
        Document={"Bytes": file_bytes},
        FeatureTypes=["TABLES"],
    )
    return response.get("Blocks", [])


def _textract_async(file_path: str) -> list:
    """Multi-page Textract call (async). Requires S3 for document storage.
    
    Used for: PDFs with 2+ pages.
    Flow: Upload to S3 → StartDocumentAnalysis → Poll → Paginate → Cleanup S3.
    Cost: $0.015/page × number of pages.
    """
    region = os.environ.get("AWS_REGION", "us-west-2")
    bucket = os.environ.get("TEXTRACT_S3_BUCKET") or os.environ.get("MENU_S3_BUCKET", "restaurant-menu-agent-webui-175918693907")

    if not bucket:
        raise ValueError(
            "TEXTRACT_S3_BUCKET or MENU_S3_BUCKET env var required for multi-page PDF processing. "
            "Textract async API requires documents to be in S3."
        )

    s3_client = boto3.client("s3", region_name=region)
    textract_client = boto3.client("textract", region_name=region)
    file_name = os.path.basename(file_path)
    s3_key = f"batch-processing/textract-temp/{uuid.uuid4().hex}/{file_name}"

    try:
        # Upload to S3 (temp prefix, auto-cleaned by lifecycle rule)
        with open(file_path, "rb") as f:
            s3_client.put_object(Bucket=bucket, Key=s3_key, Body=f.read())

        # Start async analysis
        response = textract_client.start_document_analysis(
            DocumentLocation={"S3Object": {"Bucket": bucket, "Name": s3_key}},
            FeatureTypes=["TABLES"],
        )
        job_id = response["JobId"]
        logger.info(f"Textract async job started: {job_id} for {file_name}")

        # Poll until complete, then paginate all results
        return _poll_textract_job(textract_client, job_id)

    finally:
        # Always cleanup temp S3 file
        try:
            s3_client.delete_object(Bucket=bucket, Key=s3_key)
        except Exception as e:
            logger.warning(f"Failed to cleanup S3 temp file {s3_key}: {e}")


def _poll_textract_job(client, job_id: str) -> list:
    """Poll async Textract job until SUCCEEDED, then paginate all result blocks."""
    start_time = time.time()

    # Poll loop — check status every TEXTRACT_POLL_INTERVAL seconds
    while True:
        if time.time() - start_time > TEXTRACT_POLL_TIMEOUT:
            raise TimeoutError(f"Textract job {job_id} timed out after {TEXTRACT_POLL_TIMEOUT}s")

        response = client.get_document_analysis(JobId=job_id)
        status = response["JobStatus"]

        if status == "SUCCEEDED":
            break
        elif status == "FAILED":
            raise RuntimeError(f"Textract job failed: {response.get('StatusMessage', 'Unknown')}")
        time.sleep(TEXTRACT_POLL_INTERVAL)

    # Paginate results (Textract returns max ~1000 blocks per response)
    blocks = response.get("Blocks", [])
    next_token = response.get("NextToken")
    while next_token:
        response = client.get_document_analysis(JobId=job_id, NextToken=next_token)
        blocks.extend(response.get("Blocks", []))
        next_token = response.get("NextToken")

    return blocks


def _extract_textract_tables(blocks: list) -> str:
    """Convert Textract TABLES blocks into formatted text for Haiku.
    
    Output format:
        <non-table lines: restaurant name, section headers, footer>
        
        --- MENU ITEMS (structured) ---
        Item Name Description | Price
        Item Name Description | Price
        ...
    
    This gives Haiku clear, unambiguous item-price pairing.
    Falls back to LINE blocks if no tables detected (rare for menus).
    """
    block_map = {b["Id"]: b for b in blocks}
    tables = [b for b in blocks if b["BlockType"] == "TABLE"]

    # Fallback: no tables detected — use raw LINE blocks
    if not tables:
        lines = [b.get("Text", "") for b in blocks if b.get("BlockType") == "LINE"]
        return "\n".join(lines)

    # ─── Parse table cells into row/col structure ─────────────────────────────
    cells = [b for b in blocks if b["BlockType"] == "CELL"]
    table_rows: dict[int, dict[int, str]] = {}

    for cell in cells:
        row = cell.get("RowIndex", 0)
        col = cell.get("ColumnIndex", 0)

        # Resolve cell text from child WORD blocks
        child_ids = []
        for rel in cell.get("Relationships", []):
            if rel["Type"] == "CHILD":
                child_ids.extend(rel["Ids"])

        cell_text = " ".join(
            block_map[cid]["Text"]
            for cid in child_ids
            if cid in block_map and "Text" in block_map[cid]
        ).strip()

        table_rows.setdefault(row, {})[col] = cell_text

    # ─── Identify non-table lines (section headers, restaurant name) ──────────
    # Collect all block IDs that belong to table cells
    table_block_ids = set()
    for table in tables:
        for rel in table.get("Relationships", []):
            if rel["Type"] == "CHILD":
                for cell_id in rel["Ids"]:
                    cell_block = block_map.get(cell_id)
                    if cell_block:
                        for crel in cell_block.get("Relationships", []):
                            if crel["Type"] == "CHILD":
                                table_block_ids.update(crel["Ids"])

    non_table_lines = [
        b["Text"] for b in blocks
        if b["BlockType"] == "LINE" and b["Id"] not in table_block_ids and b.get("Text", "").strip()
    ]

    # ─── Format output ────────────────────────────────────────────────────────
    output = []

    # Context lines first (restaurant name, section headers)
    if non_table_lines:
        output.extend(non_table_lines)
        output.append("")

    # Table rows as "Item | Price" — unambiguous format for Haiku
    output.append("--- MENU ITEMS (structured) ---")
    for row_num in sorted(table_rows.keys()):
        row = table_rows[row_num]
        col1 = row.get(1, "")
        col2 = row.get(2, "")
        if col1 and col2:
            output.append(f"{col1} | {col2}")
        elif col1:
            output.append(col1)

    return "\n".join(output)

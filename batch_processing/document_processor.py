"""Standalone document processor for batch processing.

Extracts and structures menu data from PDFs/images using Bedrock.
Returns structured JSON — does NOT save to DynamoDB (that's the agent's job).
Tracks token usage in _last_usage for cost reporting.
"""

import base64
import json
import logging
import os
import re
import tempfile

import boto3
from PIL import Image
from pypdf import PdfReader
from strands import tool

logger = logging.getLogger(__name__)

# Token usage tracker (reset per file by batch_process_menus.py)
_last_usage = {"input_tokens": 0, "output_tokens": 0}

# Register HEIF/HEIC support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".webp"}
HEIC_EXTENSIONS = {".heic", ".heif"}
MAX_IMAGE_DIMENSION = 2048

# Token limits for Bedrock model output
MIN_OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS_ANTHROPIC = 16384
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
def process_document(file_path: str) -> str:
    """
    Process a restaurant menu document and extract structured menu data as JSON.

    Args:
        file_path: Path to the document file

    Returns:
        Structured JSON string with restaurant name, categories, items, prices.
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
    text = _extract_pdf_text(file_path)
    if text:
        return _structure_text_with_bedrock(text)
    return _call_bedrock_vision(file_path)


def _process_heic(file_path: str) -> str:
    converted = _convert_to_jpeg(file_path)
    try:
        return _call_bedrock_vision(converted)
    finally:
        if converted != file_path:
            _safe_remove(converted)


def _process_image(file_path: str) -> str:
    resized = _resize_if_needed(file_path)
    try:
        return _call_bedrock_vision(resized)
    finally:
        if resized != file_path:
            _safe_remove(resized)


def _extract_pdf_text(file_path: str):
    try:
        reader = PdfReader(file_path)
        pages = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                pages.append(f"--- Page {i + 1} ---\n{page_text}")
        text = "\n\n".join(pages).strip()
        if len(text) < 50:
            return None
        if _is_garbled(text):
            return None
        return text
    except Exception:
        return None


def _is_garbled(text: str) -> bool:
    if len(text) < 100:
        return False
    if len(re.findall(r"/gid\d+", text)) > 20:
        return True
    readable = sum(1 for c in text if c.isalnum() or c in " .,;:!?$()-/\n")
    return (readable / len(text)) < 0.40


def _convert_to_jpeg(file_path: str) -> str:
    img = Image.open(file_path)
    if max(img.size) > MAX_IMAGE_DIMENSION:
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    img.convert("RGB").save(tmp.name, "JPEG", quality=85)
    return tmp.name


def _resize_if_needed(file_path: str) -> str:
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


def _structure_text_with_bedrock(raw_text: str) -> str:
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 2}),
    )
    # Use Haiku for structuring — much faster for text→JSON conversion
    model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    prompt = f"""Structure this restaurant menu text into JSON. Return ONLY valid JSON.

Format:
{{"restaurant_name":"...","categories":[{{"name":"...","items":[{{"name":"...","price":"12.99","description":"...","dietary_info":["vegetarian"]}}]}}],"metadata":{{"total_items":N,"price_range":{{"min":N,"max":N}},"dietary_options":["..."]}}}}

Rules: prices as strings without $, null if missing, include ALL items, group by category.

Menu text:
{raw_text}"""

    # Scale max_tokens based on input size (large menus need more output space)
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
    _last_usage["input_tokens"] += usage.get("input_tokens", 0)
    _last_usage["output_tokens"] += usage.get("output_tokens", 0)
    return body["content"][0]["text"]


def _call_bedrock_vision(file_path: str) -> str:
    """Use Bedrock Converse API for vision — works with all models (Anthropic, Nova, etc.)."""
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

    # Map extensions to Converse API format names
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
    _last_usage["input_tokens"] += usage.get("inputTokens", 0)
    _last_usage["output_tokens"] += usage.get("outputTokens", 0)
    return output


def _safe_remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _estimate_max_tokens(file_bytes: bytes, model_id: str) -> int:
    """Get max_tokens for a Bedrock vision call based on model limits.
    
    For vision calls, we can't reliably estimate output size from file size.
    Use model max to avoid truncation.
    """
    return MAX_OUTPUT_TOKENS_NOVA if "nova" in model_id.lower() else MAX_OUTPUT_TOKENS_ANTHROPIC

"""Menu regeneration tool — generates styled HTML menus based on original style + updated data."""

import json
import logging
import os
from typing import Optional

import boto3
from strands import tool

from utils import timed_tool
from s3_storage import download_to_bytes, upload_html, get_download_url

logger = logging.getLogger(__name__)

_REGION = os.environ.get("AWS_REGION", "us-west-2")
_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "restaurant-menus")
_table = boto3.resource("dynamodb", region_name=_REGION).Table(_TABLE_NAME)


def _get_menu_from_db(file_name: str) -> Optional[dict]:
    """Fetch menu from DynamoDB."""
    from menu_tools import _from_dynamodb
    resp = _table.get_item(Key={"file_name": file_name})
    item = resp.get("Item")
    return _from_dynamodb(item) if item else None


def _save_style_to_db(file_name: str, style: dict):
    """Save analyzed style to the menu's DynamoDB record."""
    from decimal import Decimal
    from menu_tools import _to_dynamodb
    _table.update_item(
        Key={"file_name": file_name},
        UpdateExpression="SET menu_style = :s",
        ExpressionAttributeValues={":s": _to_dynamodb(style)},
    )
    logger.info("Saved style analysis for %s", file_name)


STYLE_ANALYSIS_PROMPT = """Analyze this restaurant menu's visual design and style. Return a JSON object with EXACTLY these fields using ONLY the allowed values shown:
{
  "background_color": "#hex_color",
  "text_color": "#hex_color",
  "accent_color": "#hex_color",
  "heading_color": "#hex_color",
  "font_style": "serif" | "sans-serif" | "decorative",
  "layout": "single-column" | "two-column" | "three-column",
  "header_style": "centered" | "left-aligned",
  "category_style": "underlined" | "boxed" | "uppercase" | "with-divider",
  "item_style": "inline" | "stacked",
  "price_format": "$12.99" | "12.99" | "12",
  "border_style": "none" | "thin-line" | "double-line" | "ornamental",
  "section_divider": "none" | "line" | "dots" | "ornament" | "spacing-only",
  "has_descriptions": true | false,
  "has_images": true | false,
  "color_scheme": "dark-on-light" | "light-on-dark" | "colorful",
  "overall_vibe": "2-3 word description",
  "items_per_page": number (estimate how many items fit on one page/panel)
}

IMPORTANT: Use ONLY the exact allowed values listed above (separated by |). Do not use free-form descriptions.
Return ONLY valid JSON, no other text."""


def _prepare_image_for_bedrock(file_bytes: bytes, file_name: str) -> tuple[bytes, str]:
    """Prepare an image file for Bedrock — handles HEIC conversion and resizing.
    
    Returns (processed_bytes, extension) ready for the Converse API.
    """
    from document_processor import _convert_to_jpeg, _resize_if_needed
    import tempfile

    ext = os.path.splitext(file_name)[1].lower()
    tmp_files = []

    try:
        if ext in (".heic", ".heif"):
            tmp_in = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            tmp_in.write(file_bytes)
            tmp_in.close()
            tmp_files.append(tmp_in.name)
            converted = _convert_to_jpeg(tmp_in.name)
            tmp_files.append(converted)
            with open(converted, "rb") as f:
                return f.read(), ".jpg"

        elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
            tmp_in = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            tmp_in.write(file_bytes)
            tmp_in.close()
            tmp_files.append(tmp_in.name)
            resized = _resize_if_needed(tmp_in.name)
            if resized != tmp_in.name:
                tmp_files.append(resized)
            with open(resized, "rb") as f:
                return f.read(), ext

        return file_bytes, ext
    finally:
        for tmp in tmp_files:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _build_converse_media_block(file_bytes: bytes, ext: str) -> dict:
    """Build the Converse API content block for an image or document."""
    if ext == ".pdf":
        return {"document": {"format": "pdf", "name": "menu_style", "source": {"bytes": file_bytes}}}

    format_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp"}
    return {"image": {"format": format_map.get(ext, "jpeg"), "source": {"bytes": file_bytes}}}


def _parse_style_json(text: str) -> dict:
    """Parse style JSON from LLM response, handling fences and malformation."""
    try:
        if "```" in text:
            lines = text.split("\n")
            text = "\n".join(l for l in lines if not l.strip().startswith("```"))
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {"overall_vibe": "modern", "layout": "single-column"}


def _analyze_style_with_bedrock(s3_key: str, file_name: str) -> dict:
    """Analyze the visual style of a menu using Bedrock vision (Converse API).
    
    Downloads the file from S3, prepares it (resize/convert), sends to Bedrock,
    and returns a structured style dict.
    """
    from metrics import report_usage

    # Download and prepare image
    file_bytes = download_to_bytes(s3_key)
    file_bytes, ext = _prepare_image_for_bedrock(file_bytes, file_name)

    # Call Bedrock Converse API
    from botocore.config import Config
    client = boto3.client(
        "bedrock-runtime",
        region_name=_REGION,
        config=Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 3, "mode": "adaptive"}),
    )
    media_block = _build_converse_media_block(file_bytes, ext)

    response = client.converse(
        modelId=_MODEL_ID,
        messages=[{"role": "user", "content": [media_block, {"text": STYLE_ANALYSIS_PROMPT}]}],
        inferenceConfig={"maxTokens": 4096},
    )

    # Report usage and parse response
    usage = response.get("usage", {})
    report_usage(
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
        source="analyze_menu_style",
    )

    style_text = response["output"]["message"]["content"][0]["text"]
    return _parse_style_json(style_text)


def _generate_html_menu(menu_data: dict, style: dict) -> str:
    """Generate a styled HTML menu using a Python template — no LLM needed.
    
    Instant, deterministic, zero tokens, zero cost, no truncation risk.
    
    Style fields used (from _analyze_style_with_bedrock):
        - background_color → body background
        - text_color → body text, descriptions
        - accent_color → prices, category headings, tags, borders
        - heading_color → restaurant name
        - font_style → Google Font selection (serif/sans-serif/decorative)
        - layout → single-column / two-column / three-column grid
        - header_style → centered or left-aligned header
        - category_style → underlined / boxed / uppercase / with-divider
        - border_style → none / thin-line / double-line / ornamental container border
        - section_divider → line / dots / ornament / spacing between categories
        - price_format → with or without $ sign
        - items_per_page → CSS page-break-before for print pagination
        - overall_vibe → displayed as subtitle under restaurant name
    """
    restaurant = menu_data.get("restaurant_name", "Restaurant Menu")
    categories = menu_data.get("categories", [])

    # Extract style values with defaults
    bg_color = style.get("background_color", "#faf8f5")
    text_color = style.get("text_color", "#2c2c2c")
    accent_color = style.get("accent_color", "#c0392b")
    heading_color = style.get("heading_color", "#1a1a1a")
    font_style = style.get("font_style", "sans-serif")
    layout = style.get("layout", "single-column").lower()
    overall_vibe = style.get("overall_vibe", "modern clean")
    items_per_page = style.get("items_per_page", 25)  # From style analysis or default
    price_format = style.get("price_format", "$").strip()  # How prices are shown
    category_style = style.get("category_style", "underlined").lower()
    border_style = style.get("border_style", "none").lower()
    section_divider = style.get("section_divider", "line").lower()
    color_scheme = style.get("color_scheme", "dark-on-light").lower()
    header_style = style.get("header_style", "centered").lower()

    # Map font style to Google Font
    font_map = {
        "serif": ("Playfair Display", "serif"),
        "sans-serif": ("Inter", "sans-serif"),
        "decorative": ("Lobster", "cursive"),
        "traditional": ("Merriweather", "serif"),
        "modern": ("Inter", "sans-serif"),
        "elegant": ("Playfair Display", "serif"),
    }
    font_family, fallback = font_map.get(font_style.split("/")[0].strip().lower(), ("Inter", "sans-serif"))

    # Layout CSS
    if "three" in layout or "3" in layout or "grid" in layout or "multi" in layout or "4" in layout:
        layout_css = ".categories { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; } @media (max-width: 900px) { .categories { grid-template-columns: 1fr 1fr; } } @media (max-width: 600px) { .categories { grid-template-columns: 1fr; } }"
    elif "two" in layout or "2" in layout:
        layout_css = ".categories { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; } @media (max-width: 700px) { .categories { grid-template-columns: 1fr; } }"
    else:
        layout_css = ".categories { max-width: 800px; }"

    # Category heading style
    if "boxed" in category_style or "background" in category_style:
        cat_css = f"background: {accent_color}15; padding: 8px 16px; border-radius: 4px; border-bottom: none;"
    elif "uppercase" in category_style:
        cat_css = f"text-transform: uppercase; letter-spacing: 2px; font-size: 1.1em; border-bottom: 2px solid {accent_color};"
    elif "divider" in category_style or "underlined" in category_style:
        cat_css = f"border-bottom: 2px solid {accent_color}; padding-bottom: 6px;"
    else:
        cat_css = f"border-bottom: 1px solid {accent_color}33;"

    # Section divider between categories
    if "dots" in section_divider:
        divider_css = f"border-bottom: 2px dotted {accent_color}33; margin-bottom: 24px; padding-bottom: 24px;"
    elif "ornament" in section_divider:
        divider_css = f"border-bottom: 1px solid {accent_color}; margin-bottom: 28px; padding-bottom: 28px;"
    elif "line" in section_divider:
        divider_css = f"border-bottom: 1px solid {text_color}15; margin-bottom: 24px; padding-bottom: 24px;"
    else:
        divider_css = "margin-bottom: 32px;"

    # Header alignment
    header_align = "center" if "center" in header_style else "left"

    # Border around the whole menu
    container_border = ""
    if "thin" in border_style or "line" in border_style:
        container_border = f"border: 1px solid {accent_color}33; padding: 40px; border-radius: 4px;"
    elif "double" in border_style:
        container_border = f"border: 3px double {accent_color}; padding: 40px;"
    elif "ornamental" in border_style:
        container_border = f"border: 2px solid {accent_color}; padding: 40px; border-radius: 8px; box-shadow: 0 0 0 4px {bg_color}, 0 0 0 5px {accent_color}33;"

    # Build menu items HTML with page breaks
    categories_html = ""
    item_count = 0
    for cat in categories:
        items_html = ""
        for item in cat.get("items", []):
            item_count += 1
            name = item.get("name", "")
            price = item.get("price", "")
            desc = item.get("description", "")
            dietary = item.get("dietary_info", [])

            price_display = f"${price}" if price and price != "null" else ""  # Respect original price format
            if price and price != "null" and "$" not in price_format.lower():
                price_display = price  # No $ if original didn't use it
            desc_html = f'<p class="item-desc">{desc}</p>' if desc else ""
            dietary_html = ""
            if dietary:
                tags = " ".join(f'<span class="tag">{d}</span>' for d in dietary)
                dietary_html = f'<div class="dietary">{tags}</div>'

            items_html += f"""
        <div class="menu-item">
          <div class="item-header">
            <span class="item-name">{name}</span>
            <span class="item-price">{price_display}</span>
          </div>
          {desc_html}
          {dietary_html}
        </div>"""

        # Insert page break if we've exceeded items_per_page
        page_break = ""
        if item_count > items_per_page and item_count % items_per_page < len(cat.get("items", [])):
            page_break = ' style="page-break-before: always;"'

        categories_html += f"""
    <div class="category"{page_break}>
      <h2 class="category-name">{cat.get("name", "")}</h2>
      {items_html}
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{restaurant} — Menu</title>
<link href="https://fonts.googleapis.com/css2?family={font_family.replace(' ', '+')}:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: '{font_family}', {fallback}; background: {bg_color}; color: {text_color}; padding: 40px 20px; min-height: 100vh; }}
  .container {{ max-width: {1200 if 'three' in layout or 'multi' in layout else 900 if 'two' in layout else 800}px; margin: 0 auto; {container_border} }}
  .header {{ text-align: {header_align}; margin-bottom: 40px; padding-bottom: 20px; border-bottom: 2px solid {accent_color}; }}
  .header h1 {{ font-size: 2.2em; color: {heading_color}; margin-bottom: 8px; }}
  .header p {{ color: {text_color}; opacity: 0.7; font-size: 0.95em; }}
  .category {{ {divider_css} }}
  .category-name {{ font-size: 1.4em; color: {accent_color}; margin-bottom: 16px; padding-bottom: 8px; {cat_css} }}
  {layout_css}
  .menu-item {{ padding: 12px 0; border-bottom: 1px solid {text_color}08; }}
  .menu-item:last-child {{ border-bottom: none; }}
  .item-header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
  .item-name {{ font-weight: 600; font-size: 1.05em; flex: 1; }}
  .item-price {{ color: {accent_color}; font-weight: 700; font-size: 1.05em; white-space: nowrap; }}
  .item-desc {{ font-size: 0.82em; color: {text_color}; opacity: 0.6; margin-top: 4px; line-height: 1.4; }}
  .dietary {{ margin-top: 5px; }}
  .tag {{ display: inline-block; font-size: 0.68em; background: {accent_color}12; color: {accent_color}; padding: 2px 8px; border-radius: 12px; margin-right: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
  @media print {{ body {{ padding: 20px; }} .container {{ max-width: 100%; }} }}
  @media (max-width: 600px) {{ .header h1 {{ font-size: 1.6em; }} .category-name {{ font-size: 1.2em; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{restaurant}</h1>
    <p>{overall_vibe.title()}</p>
  </div>
  <div class="categories">
  {categories_html}
  </div>
  <footer style="margin-top: 40px; padding-top: 16px; border-top: 1px solid {text_color}15; text-align: center; font-size: 0.7em; color: {text_color}; opacity: 0.4;">
    Generated by Menu Assistant • Built by AWS Startup SA Team • Powered by Amazon Bedrock
  </footer>
</div>
</body>
</html>"""

    return html


# ─── Tools ────────────────────────────────────────────────────────────────────

@tool
@timed_tool
def analyze_menu_style(file_name: str) -> str:
    """
    Analyze the visual style of an original menu document stored in S3.
    Extracts colors, fonts, layout, and design elements.
    Saves the style analysis to the database for future use.

    Args:
        file_name: The menu file name to analyze (must have been previously uploaded)

    Returns:
        JSON with the style analysis results
    """
    menu = _get_menu_from_db(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    # Check if style already exists
    if menu.get("menu_style"):
        return json.dumps({
            "status": "Style already analyzed",
            "file_name": file_name,
            "style": menu["menu_style"],
        }, indent=2)

    # Check if we have the S3 reference
    s3_key = menu.get("s3_key")
    if not s3_key:
        return json.dumps({"error": f"No original file stored in S3 for '{file_name}'. Cannot analyze style."})

    # Analyze style
    style = _analyze_style_with_bedrock(s3_key, file_name)

    # Save to DB
    _save_style_to_db(file_name, style)

    return json.dumps({
        "status": "Style analyzed and saved",
        "file_name": file_name,
        "style": style,
    }, indent=2)


@tool
async def regenerate_menu_html(file_name: str) -> str:
    """
    Generate a styled HTML menu based on the stored menu data and style analysis.
    The HTML file is uploaded to S3 and a download link is provided.

    If no style has been analyzed yet, it will be analyzed first.

    Args:
        file_name: The menu file name to regenerate

    Returns:
        JSON with the download URL for the generated HTML menu
    """
    menu = _get_menu_from_db(file_name)
    if not menu:
        yield json.dumps({"error": f"No menu found for '{file_name}'."})
        return

    # Get or analyze style
    style = menu.get("menu_style")
    if not style:
        s3_key = menu.get("s3_key")
        if not s3_key:
            yield json.dumps({"error": "No original file in S3 and no style saved. Upload the original menu first."})
            return
        yield "🎨 Analyzing original menu style..."
        style = _analyze_style_with_bedrock(s3_key, file_name)
        _save_style_to_db(file_name, style)
        yield "✅ Style captured! Now generating your menu..."
    else:
        yield "🎨 Using saved style profile..."

    # Prepare menu data (exclude internal fields)
    menu_content = {k: v for k, v in menu.items() if k not in ("file_name", "processed_at", "s3_key", "menu_style")}

    # Generate HTML
    yield "✨ Generating styled HTML menu..."
    html = _generate_html_menu(menu_content, style)

    # Upload to S3
    yield "☁️ Uploading to cloud..."
    html_s3_key = upload_html(file_name, html)

    # Generate download URL
    download_url = get_download_url(html_s3_key)

    yield json.dumps({
        "status": "Menu HTML generated",
        "file_name": file_name,
        "download_url": download_url,
        "s3_key": html_s3_key,
    }, indent=2)

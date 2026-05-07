#!/usr/bin/env python3
"""Batch processor — processes all files in sample_menus/ directly (no Strands agent).

Calls document_processor functions directly for maximum speed.
Outputs <original_file_name>_menu.json files.

Usage:
    python batch_process_menus.py                     # default: all files
    python batch_process_menus.py --model haiku       # use haiku for text structuring
    python batch_process_menus.py --workers 3         # limit parallelism
"""

import argparse
import json
import logging
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from document_processor import (
    _process_pdf,
    _process_heic,
    _process_image,
    _extract_pdf_text,
    _call_bedrock_vision,
    process_with_textract,
    reset_thread_usage,
    _get_thread_usage,
    IMAGE_EXTENSIONS,
    HEIC_EXTENSIONS,
)

load_dotenv()

logging.basicConfig(
    format="%(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler()],
    level=logging.INFO,
)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

_print_lock = threading.Lock()


def _log(msg: str):
    with _print_lock:
        print(msg, flush=True)


# Supported file extensions
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".webp", ".heic", ".heif"}

# Model configurations (vision model for images/scanned PDFs)
MODELS = {
    "sonnet": {
        "model_id": "us.anthropic.claude-sonnet-4-6",
        "display_name": "Claude Sonnet 4.6",
        "input_per_1m": 3.00,
        "output_per_1m": 15.00,
        "type": "llm_vision",
    },
    "haiku": {
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "display_name": "Claude Haiku 4.5",
        "input_per_1m": 0.80,
        "output_per_1m": 4.00,
        "type": "llm_vision",
    },
    "nova-pro": {
        "model_id": "us.amazon.nova-pro-v1:0",
        "display_name": "Amazon Nova Pro",
        "input_per_1m": 0.80,
        "output_per_1m": 3.20,
        "type": "llm_vision",
    },
    "opus": {
        "model_id": "us.anthropic.claude-opus-4-7",
        "display_name": "Claude Opus 4.7",
        "input_per_1m": 15.00,
        "output_per_1m": 75.00,
        "type": "llm_vision",
    },
    "textract": {
        "model_id": "textract-tables",
        "display_name": "Textract TABLES + Haiku",
        "input_per_1m": 0.80,   # Haiku structuring cost (input tokens)
        "output_per_1m": 4.00,  # Haiku structuring cost (output tokens)
        "textract_per_page": 0.015,  # Textract TABLES feature per page
        "type": "textract",
    },
}

# Haiku pricing (used for text→JSON structuring)
HAIKU_INPUT_PER_1M = 0.80
HAIKU_OUTPUT_PER_1M = 4.00

DEFAULT_MODEL = "sonnet"
DEFAULT_WORKERS = 5


def get_sample_menus_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_menus")


def list_menu_files(directory: str) -> list[str]:
    files = []
    for f in sorted(os.listdir(directory)):
        ext = os.path.splitext(f)[1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            files.append(os.path.join(directory, f))
    return files


def build_output_path(source_file: str, output_dir: str) -> str:
    base_name = os.path.splitext(os.path.basename(source_file))[0]
    return os.path.join(output_dir, f"{base_name}_menu.json")


def process_single_file(file_path: str, output_path: str, vision_model_id: str) -> dict:
    """Process a single menu file directly — no agent, no LLM orchestration.
    
    Args:
        file_path: Path to the menu file
        output_path: Where to write the JSON output
        vision_model_id: Bedrock model ID to use for vision calls (images/scanned PDFs)
    """
    file_name = os.path.basename(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    start_time = time.time()

    # Set the vision model for this call
    os.environ["BEDROCK_MODEL_ID"] = vision_model_id

    # Reset token tracking for this file (thread-local for parallel safety)
    import document_processor as dp
    dp._last_usage = {"input_tokens": 0, "output_tokens": 0}
    dp._textract_usage = {"pages": 0}
    reset_thread_usage()

    model_key = os.environ.get("_BATCH_MODEL_KEY", "sonnet")
    model_config = MODELS[model_key]

    try:
        # Route to the correct processor based on method type
        if model_config.get("type") == "textract":
            result_text = process_with_textract(file_path)
        elif ext == ".pdf":
            result_text = _process_pdf(file_path)
        elif ext in HEIC_EXTENSIONS:
            result_text = _process_heic(file_path)
        elif ext in IMAGE_EXTENSIONS:
            result_text = _process_image(file_path)
        else:
            return {
                "file": file_name,
                "success": False,
                "error": f"Unsupported format: {ext}",
                "duration_seconds": 0,
            }

        duration = round(time.time() - start_time, 2)

        # Try to parse as JSON
        menu_data = _extract_json(result_text)

        if menu_data:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(menu_data, f, indent=2, ensure_ascii=False)

            # Count items
            total_items = sum(len(c.get("items", [])) for c in menu_data.get("categories", []))
            restaurant = menu_data.get("restaurant_name", "Unknown")

            # Get token usage (thread-local for parallel safety)
            thread_usage, textract_thread_usage = _get_thread_usage()
            if model_config.get("type") == "textract":
                input_tokens = thread_usage.get("input_tokens", 0)
                output_tokens = thread_usage.get("output_tokens", 0)
            else:
                usage = dp._last_usage
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)

            # Calculate cost based on method type
            if model_config.get("type") == "textract":
                # Textract cost = pages × per-page price + Haiku token cost
                textract_pages = textract_thread_usage.get("pages", 0)
                textract_cost = textract_pages * model_config.get("textract_per_page", 0.015)
                haiku_cost = (input_tokens * HAIKU_INPUT_PER_1M + output_tokens * HAIKU_OUTPUT_PER_1M) / 1_000_000
                cost = textract_cost + haiku_cost
            else:
                # LLM-only cost
                is_text_pdf = ext == ".pdf" and _extract_pdf_text(file_path) is not None
                if is_text_pdf:
                    cost = (input_tokens * HAIKU_INPUT_PER_1M + output_tokens * HAIKU_OUTPUT_PER_1M) / 1_000_000
                else:
                    cost = (input_tokens * MODELS[model_key]["input_per_1m"] +
                            output_tokens * MODELS[model_key]["output_per_1m"]) / 1_000_000

            result = {
                "file": file_name,
                "output_json": output_path,
                "duration_seconds": duration,
                "total_items": total_items,
                "restaurant_name": restaurant,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 5),
                "success": True,
            }

            # Add Textract-specific metadata
            if model_config.get("type") == "textract":
                result["textract_pages"] = textract_thread_usage.get("pages", 0)
                result["textract_cost_usd"] = round(textract_cost, 5)
                result["haiku_cost_usd"] = round(haiku_cost, 5)

            return result
        else:
            # Save raw text for debugging
            with open(output_path + ".raw.txt", "w", encoding="utf-8") as f:
                f.write(result_text)
            return {
                "file": file_name,
                "output_json": output_path,
                "duration_seconds": duration,
                "success": False,
                "error": "JSON extraction failed",
            }

    except Exception as e:
        duration = round(time.time() - start_time, 2)
        return {
            "file": file_name,
            "duration_seconds": duration,
            "success": False,
            "error": str(e),
        }


def _extract_json(text: str) -> dict | None:
    """Extract JSON from model response (handles markdown fences, prose wrapping, minor malformation)."""
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
            # Try fixing common issues: missing quotes before keys
            import re
            fixed = re.sub(r'(\s+)(\w+)":', r'\1"\2":', candidate)
            try:
                return json.loads(fixed)
            except (json.JSONDecodeError, TypeError):
                pass

    return None


def main():
    parser = argparse.ArgumentParser(description="Batch process restaurant menu files")
    parser.add_argument("--model", "-m", choices=list(MODELS.keys()), default=DEFAULT_MODEL,
                        help=f"Vision model for images/scanned PDFs (default: {DEFAULT_MODEL}). Haiku is always used for text→JSON.")
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--dir", "-d", type=str, default=None,
                        help="Input directory (default: sample_menus/)")
    args = parser.parse_args()

    model_config = MODELS[args.model]
    os.environ["_BATCH_MODEL_KEY"] = args.model
    sample_dir = args.dir or get_sample_menus_dir()

    # Output directory with model name
    timestamp = time.strftime("%d%m%Y-%H%M%S")
    batch_runs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_runs")
    output_dir = os.path.join(batch_runs_dir, f"run-{timestamp}-{args.model}")
    os.makedirs(output_dir, exist_ok=True)

    menu_files = list_menu_files(sample_dir)

    if not menu_files:
        print("❌ No supported menu files found")
        return

    print(f"\n{'='*70}")
    print(f"🍽️  Batch Menu Processor (Direct — No Agent)")
    print(f"{'='*70}")
    print(f"  Source directory : {sample_dir}")
    print(f"  Output directory : {output_dir}")
    print(f"  Files to process : {len(menu_files)}")
    print(f"  Parallel workers : {args.workers}")
    if model_config.get("type") == "textract":
        print(f"  Method           : Textract TABLES → Haiku structuring")
        print(f"  OCR engine       : AWS Textract (TABLES feature)")
        print(f"  Structuring      : Haiku 4.5")
    else:
        print(f"  Vision model     : {model_config['display_name']} ({model_config['model_id']})")
        print(f"  PDF structuring  : Haiku 4.5 (hardcoded)")
    print(f"{'='*70}\n")

    results = []
    total_start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for file_path in menu_files:
            output_path = build_output_path(file_path, output_dir)
            future = executor.submit(process_single_file, file_path, output_path, model_config["model_id"])
            futures[future] = os.path.basename(file_path)

        for future in as_completed(futures):
            file_name = futures[future]
            try:
                result = future.result()
                results.append(result)
                status = "✅" if result["success"] else "⚠️"
                items = result.get("total_items", 0)
                cost = result.get("cost_usd", 0)
                _log(f"  {status} {result['file']:<40} {result['duration_seconds']:>6.1f}s | {items} items | ${cost:.4f}")
            except Exception as e:
                results.append({"file": file_name, "success": False, "error": str(e), "duration_seconds": 0})
                _log(f"  ❌ {file_name}: {e}")

    total_duration = time.time() - total_start
    results.sort(key=lambda r: r["file"])

    # Summary
    successful = sum(1 for r in results if r["success"])
    total_items = sum(r.get("total_items", 0) for r in results)
    total_cost = sum(r.get("cost_usd", 0) for r in results)
    total_input = sum(r.get("input_tokens", 0) for r in results)
    total_output = sum(r.get("output_tokens", 0) for r in results)

    print(f"\n{'='*70}")
    print(f"📊 Summary")
    print(f"{'='*70}")
    print(f"  Files processed : {len(results)}")
    print(f"  Successful      : {successful}")
    print(f"  Failed          : {len(results) - successful}")
    print(f"  Total duration  : {total_duration:.1f}s")
    print(f"  Total items     : {total_items}")
    print(f"  Total tokens    : {total_input + total_output:,} (in: {total_input:,} / out: {total_output:,})")
    print(f"  Total cost      : ${total_cost:.4f}")
    print(f"{'='*70}")

    # Per-file table
    print(f"\n  {'File':<40} {'Duration':>10} {'Items':>8} {'Status':>8}")
    print(f"  {'-'*70}")
    for r in results:
        status = "OK" if r["success"] else "FAIL"
        items = r.get("total_items", 0)
        print(f"  {r['file']:<40} {r['duration_seconds']:>8.1f}s {items:>8} {status:>8}")

    # Save log
    log_path = os.path.join(output_dir, "_batch_run_log.json")
    log_data = {
        "run_timestamp": timestamp,
        "model": args.model,
        "method_type": model_config.get("type", "llm_vision"),
        "vision_model_id": model_config["model_id"],
        "vision_model_name": model_config["display_name"],
        "pdf_structuring_model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "total_duration_seconds": round(total_duration, 2),
        "parallel_workers": args.workers,
        "files_processed": len(results),
        "files_successful": successful,
        "files_failed": len(results) - successful,
        "total_items": total_items,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": round(total_cost, 5),
        "results": results,
    }
    # Add Textract-specific aggregate metrics
    if model_config.get("type") == "textract":
        total_pages = sum(r.get("textract_pages", 0) for r in results)
        total_textract_cost = sum(r.get("textract_cost_usd", 0) for r in results)
        total_haiku_cost = sum(r.get("haiku_cost_usd", 0) for r in results)
        log_data["total_textract_pages"] = total_pages
        log_data["total_textract_cost_usd"] = round(total_textract_cost, 5)
        log_data["total_haiku_cost_usd"] = round(total_haiku_cost, 5)

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)
    print(f"\n📝 Log saved to: {log_path}\n")


if __name__ == "__main__":
    main()

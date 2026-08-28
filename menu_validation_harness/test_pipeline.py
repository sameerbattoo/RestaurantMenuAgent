#!/usr/bin/env python3
"""End-to-end pipeline test harness: PDF/image -> Boons extraction -> post-validation.

Runs the REAL extraction pipeline (PyPDF text or Bedrock vision + Sonnet 4.6 with the
Boons extraction prompt) then feeds the output through the deterministic PostValidator.
Skips DynamoDB and S3 entirely, so it is safe to run locally and iterate quickly.

Usage:
    # Run a single file through the full pipeline (calls real Bedrock)
    python test_pipeline.py --file "../sample_menus/Kabila Restaurant.pdf"

    # Run every file in the sample_menus folder
    python test_pipeline.py --dir ../sample_menus

    # Save the extracted + validated JSON alongside a report
    python test_pipeline.py --file "../sample_menus/menu1.png" --out out.json

    # Skip extraction and validate a pre-extracted JSON file (no Bedrock call)
    python test_pipeline.py --validate-json extracted.json

Requires AWS credentials with Bedrock access and BEDROCK_MODEL_ID (default sonnet-4-6).
"""

import argparse
import json
import os
import sys
import time

# Fully self-contained: every module this harness imports is a sibling in this
# same folder (harness_document_processor, post_validation, expand_modifiers,
# boons_extraction_prompt). No dependency on the agent package.
_HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

from post_validation import validate_menu
from expand_modifiers import expand_menu

# Prompt version to use for extraction: "v2" (full Boons shape) or "v3" (minimal
# shape + Python expansion). Set via the --prompt-version CLI flag.
_PROMPT_VERSION = "v2"


# ─── Extraction (uses local harness_document_processor helpers) ───────────────

def _extract_raw(file_path: str, timings: dict) -> str:
    """Run the real extraction for a file, returning the raw LLM JSON text.

    Uses the Boons extraction prompt so the output matches what PostValidator expects.
    Text PDFs go through PyPDF + Sonnet structuring; images/scanned PDFs use vision.

    Records per-phase timings into the `timings` dict:
      - method            : "pdf_text" | "pdf_vision" | "image_vision" | "heic_vision"
      - pdf_text_extract   : PyPDF text extraction seconds (text PDFs only)
      - llm_structuring    : text -> JSON LLM call seconds (text PDFs only)
      - image_prep         : HEIC convert / resize seconds (image paths only)
      - vision_llm         : vision LLM call seconds (image / scanned PDF paths)
    """
    import harness_document_processor as dp
    import boons_extraction_prompt as bp

    if _PROMPT_VERSION == "v3":
        vision_prompt = bp.get_vision_extraction_prompt_v3()
        text_prompt_fn = bp.get_boons_text_structuring_prompt_v3
    else:
        vision_prompt = bp.get_vision_extraction_prompt()          # V2
        text_prompt_fn = bp.get_boons_text_structuring_prompt      # V2

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        # Phase 1a: PyPDF text extraction (deterministic, local, no LLM).
        t0 = time.time()
        pages = dp._extract_pdf_pages(file_path)
        timings["pdf_text_extract"] = time.time() - t0

        if pages:
            # Text-based PDF path.
            timings["method"] = "pdf_text"
            combined = "\n\n".join(f"--- Page {i + 1} ---\n{t}" for i, t in enumerate(pages))
            # Phase 1b: LLM structuring (text -> JSON).
            t1 = time.time()
            raw = _bedrock_text(text_prompt_fn(combined))
            timings["llm_structuring"] = time.time() - t1
            return raw

        # Scanned PDF: no extractable text -> vision.
        timings["method"] = "pdf_vision"
        t1 = time.time()
        raw = _bedrock_vision(file_path, vision_prompt)
        timings["vision_llm"] = time.time() - t1
        return raw

    if ext in dp.HEIC_EXTENSIONS:
        timings["method"] = "heic_vision"
        t0 = time.time()
        converted = dp._convert_to_jpeg(file_path)
        timings["image_prep"] = time.time() - t0
        try:
            t1 = time.time()
            raw = _bedrock_vision(converted, vision_prompt)
            timings["vision_llm"] = time.time() - t1
            return raw
        finally:
            if converted != file_path:
                dp._safe_remove(converted)

    if ext in dp.IMAGE_EXTENSIONS:
        timings["method"] = "image_vision"
        t0 = time.time()
        resized = dp._resize_if_needed(file_path)
        timings["image_prep"] = time.time() - t0
        try:
            t1 = time.time()
            raw = _bedrock_vision(resized, vision_prompt)
            timings["vision_llm"] = time.time() - t1
            return raw
        finally:
            if resized != file_path:
                dp._safe_remove(resized)

    raise ValueError(f"Unsupported file type: {ext}")


def _bedrock_client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        config=Config(read_timeout=600, connect_timeout=10,
                      retries={"max_attempts": 3, "mode": "adaptive"}),
    )


def _model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")


def _max_tokens() -> int:
    return int(os.environ.get("MAX_OUTPUT_TOKENS_ANTHROPIC", "64000"))


def _first_text_block(response) -> str:
    """Extract the text content block from a Converse response (skips thinking blocks)."""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            return block["text"]
    return response["output"]["message"]["content"][0].get("text", "")


def _bedrock_text(prompt: str) -> str:
    """Structure raw menu text into JSON via Sonnet (thinking disabled)."""
    resp = _bedrock_client().converse(
        modelId=_model_id(),
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": _max_tokens()},
        additionalModelRequestFields={"thinking": {"type": "disabled"}},
    )
    return _first_text_block(resp)


def _bedrock_vision(file_path: str, prompt: str) -> str:
    """Extract menu JSON from an image/PDF via Sonnet vision (thinking disabled)."""
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    ext = os.path.splitext(file_path)[1].lower()
    fmt = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png",
           ".gif": "gif", ".webp": "webp", ".pdf": "pdf"}.get(ext, "jpeg")

    if ext == ".pdf":
        doc_block = {"document": {"format": "pdf", "name": "menu", "source": {"bytes": file_bytes}}}
    else:
        doc_block = {"image": {"format": fmt, "source": {"bytes": file_bytes}}}

    resp = _bedrock_client().converse(
        modelId=_model_id(),
        messages=[{"role": "user", "content": [doc_block, {"text": prompt}]}],
        inferenceConfig={"maxTokens": _max_tokens()},
        additionalModelRequestFields={"thinking": {"type": "disabled"}},
    )
    return _first_text_block(resp)


def _parse_json(raw: str):
    """Parse the LLM output (handles markdown fences). Returns list/dict or None."""
    import harness_document_processor as dp
    # Reuse the repair-capable parser.
    parsed = dp._extract_json(raw)
    if parsed is not None:
        return parsed
    # _extract_json is tuned for objects; Boons output is a top-level array.
    stripped = raw.strip()
    if "```" in stripped:
        lines = [l for l in stripped.split("\n") if not l.strip().startswith("```")]
        stripped = "\n".join(lines)
    first = stripped.find("[")
    last = stripped.rfind("]")
    if first != -1 and last > first:
        try:
            return json.loads(stripped[first:last + 1])
        except json.JSONDecodeError:
            return None
    return None


# ─── Report ───────────────────────────────────────────────────────────────────

def _print_report(file_name: str, extracted, validation: dict, timings: dict):
    summary = validation["validation_summary"]
    method = timings.get("method", "n/a")

    # Total extraction time = sum of whatever phases ran for this method.
    extract_total = (
        timings.get("pdf_text_extract", 0)
        + timings.get("llm_structuring", 0)
        + timings.get("image_prep", 0)
        + timings.get("vision_llm", 0)
    )
    validate_s = timings.get("validate", 0)
    total_s = extract_total + validate_s

    print("=" * 70)
    print(f"FILE: {file_name}")
    print("=" * 70)
    print(f"  Extraction method : {method}")
    print(f"  Timing breakdown:")

    # Phase-by-phase, only showing the phases relevant to this method.
    if "pdf_text_extract" in timings:
        print(f"    1a. PyPDF text extraction : {timings['pdf_text_extract'] * 1000:8.1f} ms")
    if "llm_structuring" in timings:
        print(f"    1b. LLM structuring (text->JSON) : {timings['llm_structuring']:8.2f} s")
    if "image_prep" in timings:
        label = "HEIC->JPEG convert" if method == "heic_vision" else "image resize"
        print(f"    1a. Image prep ({label}) : {timings['image_prep'] * 1000:8.1f} ms")
    if "vision_llm" in timings:
        print(f"    1b. LLM vision (image->JSON) : {timings['vision_llm']:8.2f} s")

    print(f"    --  Extraction subtotal : {extract_total:8.2f} s")
    if "expand" in timings:
        print(f"    1c. Modifier expansion (V3) : {timings['expand'] * 1000:8.2f} ms")
    print(f"    2.  Post-validation : {validate_s * 1000:8.2f} ms")
    print(f"    ==  TOTAL : {total_s:8.2f} s")

    print(f"  Results:")
    print(f"    Categories : {len(extracted) if isinstance(extracted, list) else 'N/A'}")
    print(f"    Total items : {summary['total_items_processed']}")
    print(f"    Valid : {summary['valid_items_count']}")
    print(f"    Invalid : {summary['invalid_items_count']}")
    print(f"    Status : {summary['validation_status']}")
    print(f"    Quality : {summary['overall_quality']}")

    if validation["invalid_items"]:
        print("\n  Invalid items:")
        for inv in validation["invalid_items"][:25]:
            name = inv["item"].get("name", "?") if isinstance(inv["item"], dict) else "?"
            fields = [e["field"] for e in inv["validation_errors"]]
            print(f"    [{inv['original_category']}] '{name}': {fields}")
        extra = len(validation["invalid_items"]) - 25
        if extra > 0:
            print(f"    ... and {extra} more")
    print()


# ─── Runners ──────────────────────────────────────────────────────────────────

def run_file(file_path: str, out_path: str = None) -> dict:
    """Full pipeline for one file. Returns the validation result (with _timings attached)."""
    file_name = os.path.basename(file_path)
    timings = {}

    raw = _extract_raw(file_path, timings)

    extracted = _parse_json(raw)
    if extracted is None:
        print(f"[{file_name}] EXTRACTION PARSE FAILED. First 400 chars:\n{raw[:400]}\n")
        return {"error": "parse_failed", "file": file_name}

    # V3 emits the minimal shape → expand to the full Boons contract before validating.
    if _PROMPT_VERSION == "v3":
        te = time.time()
        extracted = expand_menu(extracted)
        timings["expand"] = time.time() - te

    t1 = time.time()
    validation = validate_menu(extracted)
    timings["validate"] = time.time() - t1

    _print_report(file_name, extracted, validation, timings)

    # Attach timings so callers (e.g. the dir aggregate) can report them.
    validation["_timings"] = timings

    if out_path:
        with open(out_path, "w") as f:
            json.dump({"extracted": extracted, "validation": validation}, f, indent=2)
        print(f"  Saved -> {out_path}\n")

    return validation


def run_dir(dir_path: str, out_dir: str = None) -> None:
    """Full pipeline for every supported file in a directory.

    If out_dir is given, each file's extracted+validation JSON is saved there as
    <basename>.json for later analysis.
    """
    import harness_document_processor as dp
    supported = {".pdf"} | dp.IMAGE_EXTENSIONS | dp.HEIC_EXTENSIONS
    files = sorted(
        (os.path.join(dir_path, f) for f in os.listdir(dir_path)
         if os.path.splitext(f)[1].lower() in supported),
        key=lambda p: os.path.basename(p).lower(),  # case-insensitive, human-expected order
    )
    if not files:
        print(f"No supported files found in {dir_path}")
        return

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    results = []
    for fp in files:
        base = os.path.basename(fp)
        out_path = os.path.join(out_dir, os.path.splitext(base)[0] + ".json") if out_dir else None
        try:
            v = run_file(fp, out_path)
            results.append((base, v))
        except Exception as e:
            print(f"[{base}] ERROR: {e}\n")
            results.append((base, {"error": str(e)}))

    # Aggregate summary with per-phase timing.
    print("=" * 100)
    print("AGGREGATE SUMMARY")
    print("=" * 100)
    header = f"  {'FILE':<34} {'METHOD':<13} {'EXTRACT':>9} {'VALIDATE':>10} {'STATUS':<6} {'VALID'}"
    print(header)
    print("  " + "-" * 96)
    for name, v in results:
        if "validation_summary" not in v:
            print(f"  {name:<34} ERROR: {v.get('error')}")
            continue
        s = v["validation_summary"]
        t = v.get("_timings", {})
        extract_total = (
            t.get("pdf_text_extract", 0) + t.get("llm_structuring", 0)
            + t.get("image_prep", 0) + t.get("vision_llm", 0)
        )
        print(f"  {name:<34} {t.get('method', 'n/a'):<13} "
              f"{extract_total:>8.2f}s {t.get('validate', 0) * 1000:>8.2f}ms "
              f"{s['validation_status']:<6} "
              f"{s['valid_items_count']}/{s['total_items_processed']} ({s['overall_quality']})")


def run_validate_json(json_path: str) -> dict:
    """Validate a pre-extracted JSON file (no Bedrock call)."""
    with open(json_path) as f:
        data = json.load(f)
    # Accept either the raw menu or a saved {"extracted": ...} wrapper.
    extracted = data.get("extracted", data) if isinstance(data, dict) else data
    t0 = time.time()
    validation = validate_menu(extracted)
    elapsed = time.time() - t0
    _print_report(os.path.basename(json_path), extracted, validation, {"validate": elapsed})
    return validation


def main():
    parser = argparse.ArgumentParser(description="End-to-end menu extraction + post-validation test harness.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Single PDF/image to run through the full pipeline")
    group.add_argument("--dir", help="Directory of menu files to process")
    group.add_argument("--validate-json", help="Validate a pre-extracted JSON file (skips Bedrock)")
    parser.add_argument("--out", help="Write extracted + validation JSON to this path (with --file)")
    parser.add_argument("--out-dir", help="Directory to write per-file JSON output (with --dir)")
    parser.add_argument("--prompt-version", choices=["v2", "v3"], default="v2",
                        help="Extraction prompt version: v2 (full Boons shape) or "
                             "v3 (minimal shape + Python expansion). Default: v2.")
    args = parser.parse_args()

    global _PROMPT_VERSION
    _PROMPT_VERSION = args.prompt_version

    if args.file:
        run_file(args.file, args.out)
    elif args.dir:
        run_dir(args.dir, args.out_dir)
    elif args.validate_json:
        run_validate_json(args.validate_json)


if __name__ == "__main__":
    main()

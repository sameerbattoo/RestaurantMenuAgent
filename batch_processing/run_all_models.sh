#!/bin/bash
# =============================================================================
# run_all_models.sh
# One-click script to run batch processing with all 4 models and generate
# a comparison report at the end.
#
# Models tested:
#   1. Sonnet 4.6 (LLM vision — best quality/cost ratio)
#   2. Opus 4.7 (LLM vision — most expensive, no quality advantage)
#   3. Nova Pro (LLM vision — cheapest but misses dietary info)
#   4. Textract TABLES + Haiku (OCR + structuring — cheapest per page)
#
# Prerequisites:
#   - AWS credentials configured
#   - TEXTRACT_S3_BUCKET env var set (or MENU_S3_BUCKET) for multi-page PDFs
#   - Python dependencies installed (pip install -r requirements.txt)
#
# Usage:
#   cd batch_processing/
#   bash run_all_models.sh
#   bash run_all_models.sh --workers 3    # limit parallelism
#   bash run_all_models.sh --dir /path/to/menus  # custom input directory
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKERS="${WORKERS:-5}"
INPUT_DIR=""

# Parse arguments (pass through to batch_process_menus.py)
while [[ $# -gt 0 ]]; do
    case $1 in
        --workers|-w)
            WORKERS="$2"
            shift 2
            ;;
        --dir|-d)
            INPUT_DIR="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: bash run_all_models.sh [--workers N] [--dir /path/to/menus]"
            exit 1
            ;;
    esac
done

# Build common args
COMMON_ARGS="--workers $WORKERS"
if [ -n "$INPUT_DIR" ]; then
    COMMON_ARGS="$COMMON_ARGS --dir $INPUT_DIR"
fi

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  🍽️  Full Model Comparison — All 4 Extraction Methods${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Workers    : $WORKERS"
echo "  Input dir  : ${INPUT_DIR:-sample_menus/ (default)}"
echo ""

FAILED=0

# ─── Run 1: Sonnet ────────────────────────────────────────────────────────────
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  [1/4] Running Sonnet 4.6 (LLM Vision)${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
python3 "$SCRIPT_DIR/batch_process_menus.py" --model sonnet $COMMON_ARGS || {
    echo -e "${RED}  ⚠️  Sonnet run failed${NC}"
    FAILED=$((FAILED + 1))
}

# ─── Run 2: Opus ──────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  [2/4] Running Opus 4.7 (LLM Vision)${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
python3 "$SCRIPT_DIR/batch_process_menus.py" --model opus $COMMON_ARGS || {
    echo -e "${RED}  ⚠️  Opus run failed${NC}"
    FAILED=$((FAILED + 1))
}

# ─── Run 3: Nova Pro ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  [3/4] Running Nova Pro (LLM Vision)${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
python3 "$SCRIPT_DIR/batch_process_menus.py" --model nova-pro $COMMON_ARGS || {
    echo -e "${RED}  ⚠️  Nova Pro run failed${NC}"
    FAILED=$((FAILED + 1))
}

# ─── Run 4: Textract ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  [4/4] Running Textract TABLES + Haiku${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Check if Textract bucket is configured
if [ -z "$TEXTRACT_S3_BUCKET" ] && [ -z "$MENU_S3_BUCKET" ]; then
    echo -e "${YELLOW}  ⚠️  TEXTRACT_S3_BUCKET not set — Textract async (multi-page PDFs) may fail.${NC}"
    echo -e "${YELLOW}     Set it with: export TEXTRACT_S3_BUCKET=<bucket-name>${NC}"
    echo -e "${YELLOW}     Or run: bash ../infra/08-setup-textract.sh${NC}"
    echo ""
fi

python3 "$SCRIPT_DIR/batch_process_menus.py" --model textract $COMMON_ARGS || {
    echo -e "${RED}  ⚠️  Textract run failed${NC}"
    FAILED=$((FAILED + 1))
}

# ─── Generate Comparison Report ───────────────────────────────────────────────
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  📊 Generating Comparison Report (last 4 runs)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

REPORT_PATH="$SCRIPT_DIR/batch_runs/comparison-report.md"
python3 "$SCRIPT_DIR/compare_runs.py" --last 4 -o "$REPORT_PATH" || {
    echo -e "${RED}  ⚠️  Report generation failed${NC}"
    FAILED=$((FAILED + 1))
}

# ─── Final Summary ────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  ✅ All Done${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════${NC}"
echo ""
if [ $FAILED -gt 0 ]; then
    echo -e "  ${YELLOW}⚠️  $FAILED model(s) had failures — check output above${NC}"
else
    echo "  All 4 models completed successfully"
fi
echo ""
echo "  Comparison report: $REPORT_PATH"
echo ""

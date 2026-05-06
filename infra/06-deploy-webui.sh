#!/bin/bash
# =============================================================================
# 04-deploy-webui.sh
# Builds the React app and deploys to S3 + invalidates CloudFront cache
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
WEBUI_DIR="${PROJECT_ROOT}/webui"

# Load config
if [ -f "${SCRIPT_DIR}/.cognito-config" ]; then
    source "${SCRIPT_DIR}/.cognito-config"
else
    echo "ERROR: Run 01-setup-cognito.sh and 03-setup-cloudfront.sh first"
    exit 1
fi

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_header() { echo -e "${BLUE}[STEP]${NC} $1"; }

# ─── Step 1: Build React App ─────────────────────────────────────────────────
print_header "Building React app"

cd "$WEBUI_DIR"
npm install
npm run build

print_status "Build complete"

# ─── Step 2: Sync to S3 ──────────────────────────────────────────────────────
print_header "Deploying to S3: $BUCKET_NAME"

aws s3 sync dist/ "s3://${BUCKET_NAME}/" --delete --exclude "menu-uploads/*"

print_status "Files synced to S3"

# ─── Step 3: Invalidate CloudFront Cache ──────────────────────────────────────
print_header "Invalidating CloudFront cache"

aws cloudfront create-invalidation \
    --distribution-id "$DISTRIBUTION_ID" \
    --paths "/*" > /dev/null

print_status "Cache invalidation started"

# ─── Done ─────────────────────────────────────────────────────────────────────
print_header "=== Web UI Deployed ==="
echo ""
echo "  URL: https://$CLOUDFRONT_DOMAIN"
echo ""

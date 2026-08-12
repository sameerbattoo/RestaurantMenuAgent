#!/bin/bash
# =============================================================================
# 01-setup-cognito.sh
# Reuses the existing Cognito User Pool AND App Client.
# No new resources are created — this script discovers and saves the config
# for use by other infra scripts.
# =============================================================================

set -e

# ─── Configuration ────────────────────────────────────────────────────────────
USER_POOL_ID="${COGNITO_USER_POOL_ID:-us-west-2_XXXXXXXXX}"
REGION="${AWS_REGION:-us-west-2}"
WEBUI_CLIENT_ID="${COGNITO_CLIENT_ID:-XXXXXXXXXX}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_header() { echo -e "${BLUE}[STEP]${NC} $1"; }

# ─── Discover Cognito Domain from the User Pool ──────────────────────────────
print_header "Discovering Cognito configuration"

DOMAIN_PREFIX=$(aws cognito-idp describe-user-pool \
    --user-pool-id "$USER_POOL_ID" \
    --region "$REGION" \
    --query 'UserPool.Domain' \
    --output text 2>/dev/null)

if [ -z "$DOMAIN_PREFIX" ] || [ "$DOMAIN_PREFIX" = "None" ]; then
    print_error "No domain configured on User Pool $USER_POOL_ID"
    print_error "Create one with: aws cognito-idp create-user-pool-domain --user-pool-id $USER_POOL_ID --domain <prefix> --region $REGION"
    exit 1
fi

COGNITO_DOMAIN="https://${DOMAIN_PREFIX}.auth.${REGION}.amazoncognito.com"

print_header "Using existing Cognito User Pool and App Client"
echo ""
echo "  User Pool ID     : $USER_POOL_ID"
echo "  Cognito Domain   : $COGNITO_DOMAIN"
echo "  App Client ID    : $WEBUI_CLIENT_ID"
echo "  Region           : $REGION"
echo "  OIDC Discovery   : https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"
echo ""

# ─── Save config for other scripts ───────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cat > "${SCRIPT_DIR}/.cognito-config" << EOF
USER_POOL_ID=$USER_POOL_ID
COGNITO_DOMAIN=$COGNITO_DOMAIN
WEBUI_CLIENT_ID=$WEBUI_CLIENT_ID
REGION=$REGION
OIDC_DISCOVERY_URL=https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration
EOF

print_status "Config saved to infra/.cognito-config"
print_status "Done — no new Cognito resources created."

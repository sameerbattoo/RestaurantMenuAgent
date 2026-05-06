#!/bin/bash
# =============================================================================
# 02.2-launch-agentcore.sh
# Launches the Restaurant Menu Agent on AgentCore Runtime and tests it
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AGENT_DIR="${PROJECT_ROOT}/agent"

# Load config
if [ -f "${SCRIPT_DIR}/.cognito-config" ]; then
    source "${SCRIPT_DIR}/.cognito-config"
fi

REGION="${REGION:-us-west-2}"
DYNAMODB_TABLE="${DYNAMODB_TABLE:-restaurant-menus}"
AGENTCORE_MEMORY_ID="${AGENTCORE_MEMORY_ID:-}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_header() { echo -e "${BLUE}[STEP]${NC} $1"; }

# ─── Launch to AgentCore Runtime ──────────────────────────────────────────────
print_header "Deploying agent to AgentCore Runtime"
print_status "Environment variables:"
print_status "  AWS_REGION=$REGION"
print_status "  DYNAMODB_TABLE=$DYNAMODB_TABLE"
print_status "  AGENTCORE_MEMORY_ID=${AGENTCORE_MEMORY_ID:-<not set>}"

cd "$AGENT_DIR"

# Build env flags
ENV_FLAGS=(
    --env "AWS_REGION=$REGION"
    --env "DYNAMODB_TABLE=$DYNAMODB_TABLE"
    --env "MENU_S3_BUCKET=${BUCKET_NAME:-restaurant-menu-agent-webui-175918693907}"
    --env "CLOUDFRONT_DOMAIN=${CLOUDFRONT_DOMAIN:-dd9h1kd8j199p.cloudfront.net}"
)

if [ -n "$AGENTCORE_MEMORY_ID" ]; then
    ENV_FLAGS+=(--env "AGENTCORE_MEMORY_ID=$AGENTCORE_MEMORY_ID")
fi

agentcore launch "${ENV_FLAGS[@]}"

print_status "Agent deployed"

# ─── Check Status ─────────────────────────────────────────────────────────────
print_header "Checking agent status"

agentcore status

# ─── Summary ──────────────────────────────────────────────────────────────────
print_header "=== AgentCore Deployment Complete ==="
echo ""
echo "  Environment:"
echo "    AWS_REGION         = $REGION"
echo "    DYNAMODB_TABLE     = $DYNAMODB_TABLE"
echo "    AGENTCORE_MEMORY_ID= ${AGENTCORE_MEMORY_ID:-<not set>}"
echo ""
echo "  Next steps:"
echo "    1. Note the agent endpoint URL from 'agentcore status'"
echo "    2. Run 03-setup-cloudfront.sh to deploy the web UI"
echo ""

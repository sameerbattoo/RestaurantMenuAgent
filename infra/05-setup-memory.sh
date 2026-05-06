#!/bin/bash
# =============================================================================
# 06-setup-memory.sh
# Creates AgentCore Memory for conversation persistence.
# Enables the agent to remember prior conversations across sessions.
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load config
if [ -f "${SCRIPT_DIR}/.cognito-config" ]; then
    source "${SCRIPT_DIR}/.cognito-config"
fi

REGION="${REGION:-us-west-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
MEMORY_NAME="restaurant_menu_agent_memory"
EXECUTION_ROLE="arn:aws:iam::${ACCOUNT_ID}:role/AmazonBedrockAgentCoreSDKRuntime-us-west-2-360d306943"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_header() { echo -e "${BLUE}[STEP]${NC} $1"; }

# ─── Check if memory already exists ──────────────────────────────────────────
print_header "Setting up AgentCore Memory"

EXISTING_MEMORY_ID=$(aws bedrock-agentcore-control list-memories \
    --region "$REGION" \
    --query "memories[?contains(id, 'restaurant_menu')].id | [0]" \
    --output text 2>/dev/null || echo "None")

if [ -n "$EXISTING_MEMORY_ID" ] && [ "$EXISTING_MEMORY_ID" != "None" ]; then
    print_status "Memory already exists: $EXISTING_MEMORY_ID"
    MEMORY_ID="$EXISTING_MEMORY_ID"
else
    print_status "Creating memory: $MEMORY_NAME"

    MEMORY_RESULT=$(aws bedrock-agentcore-control create-memory \
        --name "$MEMORY_NAME" \
        --description "Conversation memory for Restaurant Menu Assistant" \
        --memory-execution-role-arn "$EXECUTION_ROLE" \
        --event-expiry-duration 90 \
        --memory-strategies '[
            {
                "customMemoryStrategy": {
                    "name": "MenuProcessingFacts",
                    "description": "Extracts facts about menu processing requests and user preferences",
                    "namespaces": ["/users/{actorId}/facts"],
                    "configuration": {
                        "semanticOverride": {
                            "extraction": {
                                "appendToPrompt": "Extract factual information from the conversation including: menu file names processed, restaurant names, user preferences for menu formatting, dietary restrictions mentioned, and any specific instructions the user has given about how they want menus processed. Focus on concrete facts that can be referenced in future conversations.",
                                "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
                            },
                            "consolidation": {
                                "appendToPrompt": "Consolidate menu processing facts. Merge duplicate restaurant entries, keep the most recent processing results, and maintain a clear record of user preferences. Operations: AddMemory for new info, UpdateMemory to extend existing, SkipMemory if redundant.",
                                "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
                            }
                        }
                    }
                }
            },
            {
                "customMemoryStrategy": {
                    "name": "UserPreferences",
                    "description": "Captures user preferences for menu processing and formatting",
                    "namespaces": ["/users/{actorId}/preferences"],
                    "configuration": {
                        "userPreferenceOverride": {
                            "extraction": {
                                "appendToPrompt": "Extract user preferences related to: preferred menu categories, dietary restrictions, formatting preferences, language preferences for menu items, and any recurring instructions about how to process or display menu data.",
                                "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
                            },
                            "consolidation": {
                                "appendToPrompt": "Consolidate user preferences. Keep the most recent preferences, merge related ones, and remove outdated or contradicted preferences. Operations: AddMemory for new preferences, UpdateMemory to refine existing, SkipMemory if redundant.",
                                "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
                            }
                        }
                    }
                }
            }
        ]' \
        --region "$REGION" \
        --output json)

    MEMORY_ID=$(echo "$MEMORY_RESULT" | jq -r '.memory.id')
    print_status "Memory created: $MEMORY_ID"

    # Wait for strategies to become ACTIVE
    print_status "Waiting for memory strategies to become ACTIVE..."
    for i in $(seq 1 12); do
        sleep 10
        STATUS=$(aws bedrock-agentcore-control get-memory \
            --memory-id "$MEMORY_ID" \
            --region "$REGION" \
            --query "memory.strategies[].status" \
            --output text 2>/dev/null || echo "UNKNOWN")

        if echo "$STATUS" | grep -qv "CREATING"; then
            print_status "Memory strategies are ACTIVE"
            break
        fi
        echo "    Waiting... ($STATUS)"
    done
fi

# ─── Grant Memory access to the AgentCore execution role ──────────────────────
print_header "Granting AgentCore Memory permissions to execution role"

ROLE_NAME="AmazonBedrockAgentCoreSDKRuntime-us-west-2-360d306943"

if aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1; then
    MEMORY_POLICY_DOC=$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:GetMemory",
                "bedrock-agentcore:RetrieveMemories",
                "bedrock-agentcore:RetrieveMemoryRecords"
            ],
            "Resource": "arn:aws:bedrock-agentcore:${REGION}:$(aws sts get-caller-identity --query Account --output text):memory/${MEMORY_ID}"
        }
    ]
}
EOF
)

    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "AgentCoreMemoryAccess" \
        --policy-document "$MEMORY_POLICY_DOC"

    print_status "Memory access policy attached to role '$ROLE_NAME'"
else
    print_error "Role '$ROLE_NAME' not found — attach memory permissions manually"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
print_header "=== AgentCore Memory Setup Complete ==="
echo ""
echo "  Memory ID    : $MEMORY_ID"
echo "  Memory Name  : $MEMORY_NAME"
echo "  Region       : $REGION"
echo "  Strategies   :"
echo "    - MenuProcessingFacts → /users/{actorId}/facts"
echo "    - UserPreferences     → /users/{actorId}/preferences"
echo ""
echo "  Set this environment variable in the agent:"
echo "    AGENTCORE_MEMORY_ID=$MEMORY_ID"
echo ""

# Save to config
if ! grep -q "AGENTCORE_MEMORY_ID" "${SCRIPT_DIR}/.cognito-config" 2>/dev/null; then
    echo "AGENTCORE_MEMORY_ID=$MEMORY_ID" >> "${SCRIPT_DIR}/.cognito-config"
    print_status "Memory ID added to .cognito-config"
fi

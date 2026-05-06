#!/bin/bash
# =============================================================================
# 05-setup-dynamodb.sh
# Creates the DynamoDB table for persisting restaurant menu data.
# Uses on-demand (PAY_PER_REQUEST) billing — no capacity planning needed.
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load config
if [ -f "${SCRIPT_DIR}/.cognito-config" ]; then
    source "${SCRIPT_DIR}/.cognito-config"
fi

REGION="${REGION:-us-west-2}"
TABLE_NAME="restaurant-menus"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_header() { echo -e "${BLUE}[STEP]${NC} $1"; }

# ─── Check if table already exists ───────────────────────────────────────────
print_header "Setting up DynamoDB table: $TABLE_NAME"

TABLE_STATUS=$(aws dynamodb describe-table \
    --table-name "$TABLE_NAME" \
    --region "$REGION" \
    --query 'Table.TableStatus' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$TABLE_STATUS" = "ACTIVE" ]; then
    print_status "Table '$TABLE_NAME' already exists and is ACTIVE"
elif [ "$TABLE_STATUS" = "CREATING" ]; then
    print_status "Table '$TABLE_NAME' is being created, waiting..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$REGION"
    print_status "Table is now ACTIVE"
elif [ "$TABLE_STATUS" = "NOT_FOUND" ]; then
    print_status "Creating DynamoDB table: $TABLE_NAME"

    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=file_name,AttributeType=S \
        --key-schema AttributeName=file_name,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" \
        --output json > /dev/null

    print_status "Waiting for table to become ACTIVE..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$REGION"
    print_status "Table created successfully"
else
    print_error "Table is in unexpected state: $TABLE_STATUS"
    exit 1
fi

# ─── Grant DynamoDB access to the AgentCore execution role ────────────────────
print_header "Granting DynamoDB access to AgentCore execution role"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_NAME="AmazonBedrockAgentCoreSDKRuntime-us-west-2-360d306943"

# Check if role exists
if aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1; then
    POLICY_NAME="DynamoDBMenuAccess"

    POLICY_DOC=$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Scan",
                "dynamodb:Query"
            ],
            "Resource": "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE_NAME}"
        }
    ]
}
EOF
)

    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "$POLICY_NAME" \
        --policy-document "$POLICY_DOC"

    print_status "Inline policy '$POLICY_NAME' attached to role '$ROLE_NAME'"
else
    print_error "Role '$ROLE_NAME' not found — run 02-configure-agentcore.sh first"
    print_status "You can manually attach DynamoDB permissions later"
fi

# ─── Grant S3 access for menu file uploads and generated HTML ─────────────────
print_header "Granting S3 access to AgentCore execution role"

BUCKET_NAME="${BUCKET_NAME:-restaurant-menu-agent-webui-${ACCOUNT_ID}}"

if aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1; then
    S3_POLICY_DOC=$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::${BUCKET_NAME}",
                "arn:aws:s3:::${BUCKET_NAME}/menu-uploads/*"
            ]
        }
    ]
}
EOF
)

    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "S3MenuUploadsAccess" \
        --policy-document "$S3_POLICY_DOC"

    print_status "S3 policy 'S3MenuUploadsAccess' attached to role '$ROLE_NAME'"
fi

# ─── Grant Bedrock InvokeModel for document processing & menu generation ──────
print_header "Granting Bedrock access to AgentCore execution role"

if aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1; then
    BEDROCK_POLICY_DOC=$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": [
                "arn:aws:bedrock:${REGION}::foundation-model/*",
                "arn:aws:bedrock:us-east-1::foundation-model/*"
            ]
        }
    ]
}
EOF
)

    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "BedrockInvokeModelAccess" \
        --policy-document "$BEDROCK_POLICY_DOC"

    print_status "Bedrock policy 'BedrockInvokeModelAccess' attached to role '$ROLE_NAME'"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
print_header "=== DynamoDB Setup Complete ==="
echo ""
echo "  Table Name   : $TABLE_NAME"
echo "  Region       : $REGION"
echo "  Billing      : On-Demand (PAY_PER_REQUEST)"
echo "  Partition Key: file_name (String)"
echo ""
echo "  Schema:"
echo "    file_name       (PK) - Source filename (e.g., 'menu1.pdf')"
echo "    restaurant_name      - Name of the restaurant"
echo "    categories           - List of menu categories with items"
echo "    metadata             - Summary stats (item count, price range)"
echo "    processed_at         - ISO timestamp of last processing"
echo ""

# Save table name to config
if ! grep -q "DYNAMODB_TABLE" "${SCRIPT_DIR}/.cognito-config" 2>/dev/null; then
    echo "DYNAMODB_TABLE=$TABLE_NAME" >> "${SCRIPT_DIR}/.cognito-config"
    print_status "Table name added to .cognito-config"
fi

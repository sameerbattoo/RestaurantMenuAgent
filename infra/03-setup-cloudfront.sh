#!/bin/bash
# =============================================================================
# 03-setup-cloudfront.sh
# Creates S3 bucket + CloudFront distribution (with CF Function for SPA routing)
# for the React Web UI
# Modeled after the text2sql-ecommerceagent CloudFront setup
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load Cognito config
if [ -f "${SCRIPT_DIR}/.cognito-config" ]; then
    source "${SCRIPT_DIR}/.cognito-config"
else
    echo "ERROR: Run 01-setup-cognito.sh first"
    exit 1
fi

# Configuration
PROJECT_NAME="restaurant-menu-agent"
FUNCTION_NAME="${PROJECT_NAME}-ui-index-rewrite"
BUCKET_NAME="${PROJECT_NAME}-webui-$(aws sts get-caller-identity --query Account --output text)"
REGION="${REGION:-us-west-2}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_header() { echo -e "${BLUE}[STEP]${NC} $1"; }

# ─── Step 1: Create S3 Bucket ────────────────────────────────────────────────
print_header "Creating S3 bucket: $BUCKET_NAME"

if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    print_status "Bucket already exists: $BUCKET_NAME"
else
    if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket \
            --bucket "$BUCKET_NAME" \
            --region "$REGION"
    else
        aws s3api create-bucket \
            --bucket "$BUCKET_NAME" \
            --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION"
    fi
    print_status "S3 bucket created: $BUCKET_NAME"
fi

# Block public access (CloudFront will use OAC)
aws s3api put-public-access-block \
    --bucket "$BUCKET_NAME" \
    --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

print_status "Public access blocked on bucket"

# ─── Step 2: Create CloudFront OAC ───────────────────────────────────────────
print_header "Creating CloudFront Origin Access Control"

# Check if OAC already exists
EXISTING_OAC_ID=$(aws cloudfront list-origin-access-controls \
    --query "OriginAccessControlList.Items[?Name=='${PROJECT_NAME}-oac'].Id" \
    --output text 2>/dev/null || true)

if [ -n "$EXISTING_OAC_ID" ] && [ "$EXISTING_OAC_ID" != "None" ]; then
    OAC_ID="$EXISTING_OAC_ID"
    print_status "OAC already exists: $OAC_ID"
else
    OAC_ID=$(aws cloudfront create-origin-access-control \
        --origin-access-control-config '{
            "Name": "'${PROJECT_NAME}'-oac",
            "Description": "OAC for restaurant menu web UI",
            "SigningProtocol": "sigv4",
            "SigningBehavior": "always",
            "OriginAccessControlOriginType": "s3"
        }' \
        --query 'OriginAccessControl.Id' \
        --output text)
    print_status "OAC created: $OAC_ID"
fi

# ─── Step 3: Create CloudFront Function (SPA index rewrite) ──────────────────
print_header "Creating CloudFront Function for SPA routing"

# Check if function already exists
EXISTING_FUNCTION_ETAG=$(aws cloudfront describe-function \
    --name "$FUNCTION_NAME" --stage LIVE \
    --query 'ETag' --output text 2>/dev/null || true)

if [ -n "$EXISTING_FUNCTION_ETAG" ] && [ "$EXISTING_FUNCTION_ETAG" != "None" ]; then
    print_status "CloudFront Function already exists: $FUNCTION_NAME"
    FUNCTION_ARN=$(aws cloudfront describe-function \
        --name "$FUNCTION_NAME" --stage LIVE \
        --query 'FunctionSummary.FunctionMetadata.FunctionARN' --output text)
else
    # Write function code to a temp file (AWS CLI reads it as fileb://)
    FUNCTION_CODE_FILE=$(mktemp)
    cat > "$FUNCTION_CODE_FILE" << 'FUNCEOF'
function handler(event) {
  var request = event.request;
  var uri = request.uri;

  // Rewrite paths without file extensions to index.html (SPA routing)
  if (!uri.includes(".")) {
    request.uri = "/index.html";
  } else if (uri.endsWith("/")) {
    request.uri += "index.html";
  }

  return request;
}
FUNCEOF

    # Create the function
    FUNCTION_RESULT=$(aws cloudfront create-function \
        --name "$FUNCTION_NAME" \
        --function-config '{"Comment":"Rewrite paths to index.html for SPA routing","Runtime":"cloudfront-js-2.0"}' \
        --function-code "fileb://${FUNCTION_CODE_FILE}" \
        --output json)

    rm -f "$FUNCTION_CODE_FILE"

    FUNCTION_ETAG=$(echo "$FUNCTION_RESULT" | jq -r '.ETag')
    FUNCTION_ARN=$(echo "$FUNCTION_RESULT" | jq -r '.FunctionSummary.FunctionMetadata.FunctionARN')

    # Publish the function to LIVE stage
    aws cloudfront publish-function \
        --name "$FUNCTION_NAME" \
        --if-match "$FUNCTION_ETAG" > /dev/null

    print_status "CloudFront Function created and published: $FUNCTION_NAME"
fi

print_status "Function ARN: $FUNCTION_ARN"

# ─── Step 4: Create CloudFront Distribution ───────────────────────────────────
print_header "Creating CloudFront Distribution"

DISTRIBUTION=$(aws cloudfront create-distribution \
    --distribution-config '{
        "CallerReference": "'${PROJECT_NAME}'-'$(date +%s)'",
        "Comment": "Restaurant Menu Agent Web UI",
        "DefaultRootObject": "",
        "HttpVersion": "http2and3",
        "PriceClass": "PriceClass_100",
        "DefaultCacheBehavior": {
            "TargetOriginId": "S3-'${BUCKET_NAME}'",
            "ViewerProtocolPolicy": "redirect-to-https",
            "Compress": true,
            "AllowedMethods": {
                "Quantity": 2,
                "Items": ["HEAD", "GET"],
                "CachedMethods": {"Quantity": 2, "Items": ["HEAD", "GET"]}
            },
            "ForwardedValues": {
                "QueryString": false,
                "Cookies": {"Forward": "none"}
            },
            "FunctionAssociations": {
                "Quantity": 1,
                "Items": [{
                    "FunctionARN": "'${FUNCTION_ARN}'",
                    "EventType": "viewer-request"
                }]
            },
            "MinTTL": 0,
            "DefaultTTL": 86400,
            "MaxTTL": 31536000
        },
        "Origins": {
            "Quantity": 1,
            "Items": [{
                "Id": "S3-'${BUCKET_NAME}'",
                "DomainName": "'${BUCKET_NAME}'.s3.'${REGION}'.amazonaws.com",
                "OriginPath": "",
                "S3OriginConfig": {"OriginAccessIdentity": ""},
                "OriginAccessControlId": "'${OAC_ID}'"
            }]
        },
        "Enabled": true,
        "IsIPV6Enabled": true,
        "Restrictions": {
            "GeoRestriction": {"RestrictionType": "none", "Quantity": 0}
        },
        "ViewerCertificate": {
            "CloudFrontDefaultCertificate": true,
            "MinimumProtocolVersion": "TLSv1.2_2021"
        }
    }' \
    --output json)

DISTRIBUTION_ID=$(echo "$DISTRIBUTION" | jq -r '.Distribution.Id')
CLOUDFRONT_DOMAIN=$(echo "$DISTRIBUTION" | jq -r '.Distribution.DomainName')

print_status "CloudFront Distribution: $DISTRIBUTION_ID"
print_status "CloudFront Domain: $CLOUDFRONT_DOMAIN"

# ─── Step 5: Add S3 Bucket Policy for CloudFront ─────────────────────────────
print_header "Adding S3 bucket policy for CloudFront access"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

aws s3api put-bucket-policy \
    --bucket "$BUCKET_NAME" \
    --policy '{
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowCloudFrontServicePrincipal",
            "Effect": "Allow",
            "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::'${BUCKET_NAME}'/*",
            "Condition": {
                "StringEquals": {
                    "AWS:SourceArn": "arn:aws:cloudfront::'${ACCOUNT_ID}':distribution/'${DISTRIBUTION_ID}'"
                }
            }
        }]
    }'

print_status "Bucket policy applied"

# ─── Step 6: Update Cognito Callback URLs ─────────────────────────────────────
print_header "Updating Cognito Web UI client with CloudFront callback URL"

aws cognito-idp update-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-id "$WEBUI_CLIENT_ID" \
    --allowed-o-auth-flows "code" \
    --allowed-o-auth-scopes "openid" "email" "profile" \
    --allowed-o-auth-flows-user-pool-client \
    --supported-identity-providers "COGNITO" \
    --callback-urls "http://localhost:5173/callback" "https://${CLOUDFRONT_DOMAIN}/callback" \
    --logout-urls "http://localhost:5173" "https://${CLOUDFRONT_DOMAIN}" \
    --region "$REGION" > /dev/null

print_status "Cognito callback URLs updated"

# ─── Output ──────────────────────────────────────────────────────────────────
print_header "=== CloudFront Setup Complete ==="
echo ""
echo "  S3 Bucket             : $BUCKET_NAME"
echo "  CloudFront Dist ID    : $DISTRIBUTION_ID"
echo "  CloudFront Domain     : https://$CLOUDFRONT_DOMAIN"
echo "  CF Function           : $FUNCTION_NAME"
echo ""
echo "  Next steps:"
echo "    1. Build the React app: cd webui && npm run build"
echo "    2. Deploy: aws s3 sync webui/dist/ s3://$BUCKET_NAME/"
echo "    3. Invalidate cache: aws cloudfront create-invalidation --distribution-id $DISTRIBUTION_ID --paths '/*'"
echo ""

# Save CloudFront config
cat >> "${SCRIPT_DIR}/.cognito-config" << EOF
BUCKET_NAME=$BUCKET_NAME
DISTRIBUTION_ID=$DISTRIBUTION_ID
CLOUDFRONT_DOMAIN=$CLOUDFRONT_DOMAIN
CF_FUNCTION_NAME=$FUNCTION_NAME
EOF

print_status "Config updated in infra/.cognito-config"

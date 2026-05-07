"""S3 storage for original menu files."""

import logging
import os

import boto3

logger = logging.getLogger(__name__)

_BUCKET = os.environ.get("MENU_S3_BUCKET", "restaurant-menu-agent-webui-175918693907")
_REGION = os.environ.get("AWS_REGION", "us-west-2")
_CLOUDFRONT_DOMAIN = os.environ.get("CLOUDFRONT_DOMAIN", "dd9h1kd8j199p.cloudfront.net")
_s3 = boto3.client("s3", region_name=_REGION)

# Prefix for uploaded originals
_ORIGINALS_PREFIX = "menu-uploads/originals"
# Prefix for generated HTML menus
_GENERATED_PREFIX = "menu-uploads/generated"


def upload_file(file_path: str, file_name: str) -> str:
    """Upload a file to S3 and return the S3 key.

    Args:
        file_path: Local path to the file
        file_name: Original file name (used in S3 key)

    Returns:
        The S3 key where the file was stored
    """
    safe_name = os.path.basename(file_name)
    s3_key = f"{_ORIGINALS_PREFIX}/{safe_name}"

    _s3.upload_file(file_path, _BUCKET, s3_key)
    logger.info("Uploaded %s to s3://%s/%s", safe_name, _BUCKET, s3_key)
    return s3_key


def get_download_url(s3_key: str) -> str:
    """Get the CloudFront URL for a file.

    Args:
        s3_key: The S3 object key

    Returns:
        Public CloudFront URL (properly encoded)
    """
    from urllib.parse import quote
    encoded_key = quote(s3_key, safe="/")
    return f"https://{_CLOUDFRONT_DOMAIN}/{encoded_key}"


def download_to_bytes(s3_key: str) -> bytes:
    """Download a file from S3 and return its bytes.

    Args:
        s3_key: The S3 object key

    Returns:
        File content as bytes
    """
    response = _s3.get_object(Bucket=_BUCKET, Key=s3_key)
    return response["Body"].read()


def upload_html(file_name: str, html_content: str) -> str:
    """Upload generated HTML menu to S3 with a unique timestamp-based key.

    Args:
        file_name: Base file name (e.g., "IMG_4475.HEIC")
        html_content: The HTML string to upload

    Returns:
        The S3 key where the HTML was stored (unique per generation)
    """
    import time
    safe_name = os.path.basename(file_name)
    base = os.path.splitext(safe_name)[0]
    timestamp = int(time.time())
    s3_key = f"{_GENERATED_PREFIX}/{base}_menu_{timestamp}.html"

    _s3.put_object(
        Bucket=_BUCKET,
        Key=s3_key,
        Body=html_content.encode("utf-8"),
        ContentType="text/html",
    )
    logger.info("Uploaded generated HTML to s3://%s/%s", _BUCKET, s3_key)
    return s3_key

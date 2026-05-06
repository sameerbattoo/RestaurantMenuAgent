"""File handling for uploaded menu documents.

Manages saving base64-encoded uploads to temp storage and cleanup after processing.
"""

import base64
import logging
import os
import shutil
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class UploadedFileHandler:
    """Handles saving uploaded files to temp storage and cleanup.

    Usage:
        handler = UploadedFileHandler()
        handler.save_files(files)  # files = [{"name": "...", "data": "base64...", "type": "..."}]
        # ... process files using handler.file_paths ...
        handler.cleanup()

    Or as a context manager:
        with UploadedFileHandler() as handler:
            handler.save_files(files)
            # ... process files ...
        # auto-cleanup on exit
    """

    def __init__(self):
        self._temp_dir: Optional[str] = None
        self._file_paths: list[str] = []
        self._s3_keys: dict[str, str] = {}  # file_name -> s3_key

    @property
    def temp_dir(self) -> Optional[str]:
        """Path to the temp directory, or None if no files saved."""
        return self._temp_dir

    @property
    def file_paths(self) -> list[str]:
        """List of saved file paths."""
        return self._file_paths

    @property
    def s3_keys(self) -> dict[str, str]:
        """Mapping of file_name -> s3_key for uploaded files."""
        return self._s3_keys

    def save_files(self, files: list[dict]) -> list[str]:
        """Save base64-encoded file dicts to a temp directory and upload to S3.

        Args:
            files: List of dicts with keys: name, data (base64), type

        Returns:
            List of absolute file paths where files were saved.
        """
        if not files:
            return []

        self._temp_dir = tempfile.mkdtemp(prefix="menu_upload_")
        self._file_paths = []

        for f in files:
            file_name = os.path.basename(f.get("name", "upload"))
            file_data = base64.b64decode(f.get("data", ""))
            file_path = os.path.join(self._temp_dir, file_name)

            with open(file_path, "wb") as fh:
                fh.write(file_data)

            self._file_paths.append(file_path)
            logger.debug("Saved upload: %s (%d bytes)", file_name, len(file_data))

            # Upload to S3 for permanent storage
            try:
                from s3_storage import upload_file
                s3_key = upload_file(file_path, file_name)
                self._s3_keys[file_name] = s3_key
            except Exception as exc:
                logger.warning("S3 upload failed for %s: %s", file_name, exc)

        logger.info("Saved %d file(s) to %s", len(self._file_paths), self._temp_dir)
        return self._file_paths

    def cleanup(self):
        """Remove the temp directory and all uploaded files."""
        if self._temp_dir and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            logger.debug("Cleaned up temp dir: %s", self._temp_dir)
        self._temp_dir = None
        self._file_paths = []
        # Note: s3_keys intentionally NOT cleared — they're needed after cleanup

    def build_prompt_suffix(self) -> str:
        """Build the prompt text listing uploaded file paths.

        Returns:
            Formatted string to append to the user query, or empty string if no files.
        """
        if not self._file_paths:
            return ""

        file_list = "\n".join(f"  - {p}" for p in self._file_paths)
        return (
            f"\n\nThe user uploaded the following menu file(s):\n{file_list}\n\n"
            f"Please process these menu files."
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False

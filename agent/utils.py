"""Shared utilities for the Restaurant Menu Agent."""

import functools
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)


def timed_tool(func):
    """Decorator that logs timestamps and elapsed time for tool calls."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tool_name = func.__name__
        start_ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        logger.info("[%s] Tool '%s' STARTED", start_ts, tool_name)
        start_time = time.time()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = time.time() - start_time
            end_ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            logger.info("[%s] Tool '%s' FINISHED (%.3fs)", end_ts, tool_name, elapsed)
    return wrapper

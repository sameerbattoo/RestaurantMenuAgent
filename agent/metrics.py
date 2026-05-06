"""Token usage metrics accumulator.

Provides a thread-safe callback for tracking Bedrock token usage
across all components (agent orchestration, document processing, menu generation).
"""

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class TokenAccumulator:
    """Accumulates token usage from all Bedrock calls in a request."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        """Reset counters for a new request."""
        with self._lock:
            self._input_tokens = 0
            self._output_tokens = 0
            self._tool_calls: list[dict] = []

    def add(
        self,
        input_tokens: int,
        output_tokens: int,
        source: str = "unknown",
    ):
        """Add token usage from a Bedrock call.

        Args:
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens used
            source: Which component made the call (e.g., "process_document", "style_analysis")
        """
        with self._lock:
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens
            self._tool_calls.append({
                "source": source,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            })
        logger.debug("Token usage [%s]: in=%d out=%d", source, input_tokens, output_tokens)

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    @property
    def total_tokens(self) -> int:
        return self._input_tokens + self._output_tokens

    @property
    def tool_calls(self) -> list[dict]:
        return self._tool_calls.copy()

    def get_summary(self) -> dict:
        """Get a summary of all accumulated usage."""
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self.total_tokens,
            "breakdown": self._tool_calls.copy(),
        }


# Module-level singleton per request — set by the entrypoint before each request.
_current_accumulator: Optional[TokenAccumulator] = None


def set_current_accumulator(acc: TokenAccumulator):
    """Set the active accumulator for the current request."""
    global _current_accumulator
    _current_accumulator = acc


def get_current_accumulator() -> Optional[TokenAccumulator]:
    """Get the active accumulator (may be None if not in a request context)."""
    return _current_accumulator


def report_usage(input_tokens: int, output_tokens: int, source: str = "unknown"):
    """Report token usage to the current accumulator (if active).

    This is the function that tools call after making direct Bedrock calls.
    """
    acc = _current_accumulator
    if acc:
        acc.add(input_tokens, output_tokens, source)

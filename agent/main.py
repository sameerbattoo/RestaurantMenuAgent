#!/usr/bin/env python3
"""Restaurant Menu Processing Agent — deployed on AWS Bedrock AgentCore Runtime.

Architecture:
- Session-based agent caching for conversation persistence within a container
- Async generator entrypoint for SSE streaming
- AgentCore Memory hook for cross-session/cross-container context
- DynamoDB for menu data persistence
"""

import json
import logging
import os
import sys
import time
import uuid
from typing import Dict

from dotenv import load_dotenv
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from document_processor import process_document
from file_handler import UploadedFileHandler
from memory_hook import MenuMemoryHook
from metrics import TokenAccumulator, set_current_accumulator
from menu_tools import (
    add_menu_item,
    delete_menu,
    export_menu_json,
    get_current_menu,
    list_restaurant_menus,
    merge_menu,
    remove_menu_item,
    rename_category,
    rename_restaurant,
    update_menu_item,
)
from menu_generator import analyze_menu_style, regenerate_menu_html

# ════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
    level=logging.INFO,
)
for lib in ("boto3", "botocore", "urllib3", "httpx"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("RestaurantMenuAgent")

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# Sliding window: each menu processing turn uses ~8-12 internal messages,
# so 20 gives room for ~2-3 full processing turns in context.
SLIDING_WINDOW_SIZE = int(os.environ.get("SLIDING_WINDOW_SIZE", "20"))

# Session cache TTL (seconds). AgentCore idle timeout is 900s.
SESSION_MAX_AGE = 1800

SYSTEM_PROMPT = """\
You are a friendly Restaurant Menu Assistant. You help users process
restaurant menu documents, organize menu items, and manage their menu database.

**Important: Never expose technical details like JSON schemas, internal tool
names, or implementation details to the user. Communicate naturally.**

## Capabilities:
1. **Process menu files** — Extract dishes, prices, descriptions from PDFs/images
2. **Show stored menus** — List all menus saved in the database
3. **Edit menus** — Add, remove, update items, rename restaurants/categories
4. **Merge menus** — Combine multiple files from the same restaurant into one entry
5. **Delete menus** — Remove a restaurant menu from the database entirely
6. **Export menus** — Provide full structured data for any stored menu
7. **Regenerate menu** — Create a styled HTML version of the menu matching the original design

## When a user uploads a menu file:

1. Call process_document — it extracts, structures, and checks for conflicts automatically.
   - **Multiple files? Call process_document on ALL simultaneously (parallel tool calls).**
   - If the restaurant is NEW → saves automatically, returns summary.
   - If the restaurant ALREADY EXISTS → returns a conflict report with a recommendation:
     - "overwrite" = most items already exist (re-upload of same menu)
     - "merge" = new items found (additional page/section)
   - Tell the user what was found and ask what they want to do.
   - Then call process_document again with action="overwrite" or action="merge".
2. Report each file's results to the user.

## Presentation:
- Use markdown tables for menu data
- Show categories, item counts, price ranges
- Highlight dietary options (vegetarian, vegan, gluten-free)
- When listing menus, ALWAYS include the source_files URLs as clickable markdown links in the table (e.g., a "Source" column). Show the actual file name as the link text (e.g., `[IMG_4477.HEIC](url)`) — never use generic numbered labels. These are the original uploaded files — never omit them.
- Be concise but complete

## Editing:
- Load with get_current_menu, modify with add/remove/update_menu_item
- Use rename_restaurant or rename_category for name changes
- Confirm what changed

## Merging:
- When a new file is from the same restaurant as an existing entry, offer to merge
- Use merge_menu to combine — it adds new categories/items without duplicates
- Tracks all source files in metadata.merged_from

## Regenerating a menu:
- When the user asks to regenerate/download a styled menu, use regenerate_menu_html
- This analyzes the original file's style (if not already done) and generates HTML
- Provide the download link to the user
- If the user wants to re-analyze the style, use analyze_menu_style explicitly

## Style:
- Concise, helpful, markdown-formatted
- **Funny and cheerful** — use food puns, emojis, and a warm personality
- Never mention JSON, schemas, tools, or internals
- Explain errors simply
"""

TOOLS = [
    process_document,
    get_current_menu,
    list_restaurant_menus,
    add_menu_item,
    remove_menu_item,
    update_menu_item,
    rename_restaurant,
    rename_category,
    merge_menu,
    delete_menu,
    export_menu_json,
    analyze_menu_style,
    regenerate_menu_html,
]


# ════════════════════════════════════════════════════════════════
# SESSION MANAGEMENT
# ════════════════════════════════════════════════════════════════

class SessionManager:
    """Caches Agent instances per session for conversation persistence."""

    def __init__(self, max_age: int = SESSION_MAX_AGE):
        self._agents: Dict[str, Agent] = {}
        self._timestamps: Dict[str, float] = {}
        self._max_age = max_age

    def get_or_create(self, session_id: str, actor_id: str, user_name: str = "") -> Agent:
        """Return cached agent or create a new one."""
        self._evict_expired()

        if session_id in self._agents:
            self._timestamps[session_id] = time.time()
            logger.info("Reusing agent session=%s (messages=%d)",
                        session_id[:12], len(self._agents[session_id].messages))
            return self._agents[session_id]

        agent = self._create_agent(session_id, actor_id, user_name)
        self._agents[session_id] = agent
        self._timestamps[session_id] = time.time()
        logger.info("Created new agent session=%s", session_id[:12])
        return agent

    def _create_agent(self, session_id: str, actor_id: str, user_name: str = "") -> Agent:
        """Build a new Agent with memory hook and tools."""
        hooks = []
        if AGENTCORE_MEMORY_ID:
            try:
                from bedrock_agentcore.memory import MemoryClient
                memory_hook = MenuMemoryHook(
                    memory_client=MemoryClient(region_name=AWS_REGION),
                    memory_id=AGENTCORE_MEMORY_ID,
                    actor_id=actor_id,
                    session_id=session_id,
                )
                hooks.append(memory_hook)
            except Exception as exc:
                logger.warning("Memory hook init failed: %s", exc)

        # Personalize system prompt with user info
        prompt = SYSTEM_PROMPT
        if user_name:
            prompt += f"\n\nThe current user's name is: {user_name}. Use their name occasionally to be personal and friendly."

        return Agent(
            system_prompt=prompt,
            tools=TOOLS,
            hooks=hooks,
            conversation_manager=SlidingWindowConversationManager(
                window_size=SLIDING_WINDOW_SIZE,
            ),
        )

    def _evict_expired(self):
        """Remove sessions older than max_age."""
        now = time.time()
        expired = [s for s, t in self._timestamps.items() if now - t > self._max_age]
        for s in expired:
            self._agents.pop(s, None)
            self._timestamps.pop(s, None)
        if expired:
            logger.info("Evicted %d expired sessions", len(expired))


# Module-level singleton — survives across requests within the same container.
_session_manager = SessionManager()


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def _extract_session_id(payload: dict, context) -> str:
    """Extract session ID from context (preferred) or payload (fallback)."""
    session_id = getattr(context, "session_id", None)
    if not session_id:
        headers = getattr(context, "request_headers", {}) or {}
        session_id = headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
    if not session_id:
        session_id = payload.get("session_id", str(uuid.uuid4()))
        logger.warning("No session_id in context, using: %s", session_id[:12])
    return session_id


def _extract_actor_id(context) -> str:
    """Extract actor ID from request headers, falling back to JWT username.
    
    Sanitizes the ID to only contain alphanumeric, hyphens, and underscores
    (AgentCore Memory API requirement).
    """
    import re
    headers = getattr(context, "request_headers", {}) or {}
    actor = headers.get("x-amzn-bedrock-agentcore-runtime-custom-actorid")
    if not actor:
        # Fallback: use the username from JWT
        actor = _extract_user_name(context) or "default_user"
    # Sanitize: replace dots and special chars with underscores
    return re.sub(r'[^a-zA-Z0-9_-]', '_', actor)


def _extract_user_name(context) -> str:
    """Extract user's display name from the Authorization JWT token (access token)."""
    headers = getattr(context, "request_headers", {}) or {}
    auth_header = headers.get("Authorization") or headers.get("authorization", "")
    if not auth_header:
        return ""
    try:
        import jwt
        token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else auth_header
        claims = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
        username = claims.get("username", "")
        if username.startswith("federate_"):
            return username.replace("federate_", "")
        return username or claims.get("email", "") or claims.get("name", "")
    except Exception:
        return ""


def _build_metrics(agent_result, duration_ms: int, accumulator: TokenAccumulator) -> dict:
    """Extract token metrics from AgentResult + tool-level Bedrock calls."""
    # Agent orchestration tokens (from Strands)
    agent_input = 0
    agent_output = 0

    if agent_result and hasattr(agent_result, "metrics"):
        try:
            summary = agent_result.metrics.get_summary()
            usage = summary.get("accumulated_usage", {})
            agent_input = usage.get("inputTokens", 0)
            agent_output = usage.get("outputTokens", 0)
        except Exception:
            pass

    # Tool-level Bedrock tokens (from direct InvokeModel calls)
    tool_input = accumulator.input_tokens
    tool_output = accumulator.output_tokens

    # Total
    total_input = agent_input + tool_input
    total_output = agent_output + tool_output
    cost = (total_input * 3.0 + total_output * 15.0) / 1_000_000

    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "cost_usd": round(cost, 5),
        "model": MODEL_ID,
        "duration_ms": duration_ms,
        "breakdown": {
            "agent": {"input": agent_input, "output": agent_output},
            "tools": accumulator.tool_calls,
        },
    }


# ════════════════════════════════════════════════════════════════
# AGENTCORE ENTRYPOINT
# ════════════════════════════════════════════════════════════════

agentcore_app = BedrockAgentCoreApp()


@agentcore_app.entrypoint
async def main(payload, context):
    """Async generator entrypoint — yields SSE streaming chunks."""
    file_handler = UploadedFileHandler()
    accumulator = TokenAccumulator()
    set_current_accumulator(accumulator)

    try:
        # Parse request
        user_query = payload.get("text", payload.get("prompt", payload.get("user_input", "")))
        files = payload.get("files", [])
        session_id = _extract_session_id(payload, context)
        actor_id = _extract_actor_id(context)
        user_name = _extract_user_name(context)

        logger.info("Request: session=%s actor=%s user=%s files=%d query=%s",
                    session_id[:12], actor_id[:20], user_name[:20], len(files), user_query[:80])

        # Handle file uploads
        if files:
            file_handler.save_files(files)
            user_query += file_handler.build_prompt_suffix()

        # Get or create agent (preserves conversation within container lifetime)
        agent = _session_manager.get_or_create(session_id, actor_id, user_name)

        # Stream response
        start_time = time.time()
        last_tool_key = None
        agent_result = None

        async for event in agent.stream_async(user_query):
            if "data" in event:
                yield event["data"]
            elif "current_tool_use" in event:
                tool_info = event["current_tool_use"]
                tool_name = tool_info.get("name", "")
                tool_id = tool_info.get("toolUseId", "")
                key = f"{tool_name}:{tool_id}"
                if tool_name and key != last_tool_key:
                    last_tool_key = key
                    yield f"\n[TOOL USE]{tool_name}\n"
            elif "tool_stream_event" in event:
                stream_data = event["tool_stream_event"].get("data", "")
                if stream_data and not stream_data.startswith("{"):
                    yield f"\n[TOOL USE]{stream_data}\n"
            elif "result" in event:
                agent_result = event["result"]

        # Emit metrics
        duration_ms = round((time.time() - start_time) * 1000)
        metrics = _build_metrics(agent_result, duration_ms, accumulator)
        yield f"\n[METRICS]{json.dumps(metrics)}\n"

    except Exception as exc:
        logger.error("Request failed: %s", exc, exc_info=True)
        yield f"I encountered an error processing your request. Please try again."

    finally:
        file_handler.cleanup()


if __name__ == "__main__":
    agentcore_app.run()

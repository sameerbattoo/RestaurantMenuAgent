"""AgentCore Memory hook — persists conversations and retrieves user context.

On agent initialization:
  1. Loads the last K conversation turns from memory
  2. Retrieves user preferences and facts from long-term memory
  3. Injects both into the agent's context

On each message:
  1. Saves the message to AgentCore Memory for future retrieval

Long-term memory strategies (configured via 06-setup-memory.sh):
  - MenuProcessingFacts: extracts menu/restaurant facts → /users/{actorId}/facts
  - UserPreferences: extracts user preferences → /users/{actorId}/preferences
"""

import logging

from strands.hooks.events import MessageAddedEvent, AgentInitializedEvent
from strands.hooks.registry import HookProvider
from bedrock_agentcore.memory import MemoryClient

logger = logging.getLogger(__name__)

MAX_LAST_CONVERSATIONS = 5
MAX_FACTS = 5
MAX_PREFERENCES = 5


class MenuMemoryHook(HookProvider):
    """Memory hook for persisting conversations to AgentCore Memory."""

    def __init__(
        self,
        memory_client: MemoryClient,
        memory_id: str,
        actor_id: str,
        session_id: str,
    ) -> None:
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.actor_id = actor_id
        self.session_id = session_id

    # ── Hook callbacks ────────────────────────────────────────────

    def on_agent_initialized(self, event: AgentInitializedEvent) -> None:
        """Load recent conversation history and user context when agent starts."""
        try:
            self._load_conversation_history(event)
            self._add_user_context_to_system_prompt(event)
        except Exception as exc:
            logger.error("Memory load error: %s", exc)

    def on_message_added(self, event: MessageAddedEvent) -> None:
        """Store each user/assistant message in AgentCore Memory."""
        try:
            messages = event.agent.messages
            if not messages:
                return

            last_message = messages[-1]

            # Only save user and assistant messages
            if last_message["role"] not in ("user", "assistant"):
                return

            # Check for text content
            content_blocks = last_message.get("content", [])
            if not content_blocks or "text" not in content_blocks[0]:
                return

            content = content_blocks[0]["text"]
            # Strands uses MessageRole enum; AgentCore expects "USER" or "ASSISTANT"
            role_str = str(last_message["role"]).split(".")[-1].upper()

            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[(content, role_str)],
            )
            logger.debug("Saved %s message to memory (session=%s)", role_str, self.session_id)

        except Exception as exc:
            logger.error("Memory save error: %s", exc)

    def register_hooks(self, registry) -> None:
        """Register hook callbacks with the Strands hook registry."""
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)

    # ── Internal ──────────────────────────────────────────────────

    def _load_conversation_history(self, event: AgentInitializedEvent) -> None:
        """Load the last K conversation turns from memory."""
        recent_turns = self.memory_client.get_last_k_turns(
            memory_id=self.memory_id,
            actor_id=self.actor_id,
            session_id=self.session_id,
            k=MAX_LAST_CONVERSATIONS,
        )

        if not recent_turns:
            return

        context_messages = []
        for turn in recent_turns:
            for message in turn:
                # AgentCore returns uppercase roles ("ASSISTANT", "USER")
                role = "assistant" if message["role"] == "ASSISTANT" else "user"
                content = message["content"]["text"]
                context_messages.append(
                    {"role": role, "content": [{"text": content}]}
                )

        event.agent.messages = context_messages
        logger.info(
            "Loaded %d messages from memory (session=%s)",
            len(context_messages), self.session_id,
        )

    def _add_user_context_to_system_prompt(self, event: AgentInitializedEvent) -> None:
        """Retrieve user preferences and facts and inject into system prompt."""
        try:
            # Get user preferences
            preferences = self.memory_client.retrieve_memories(
                memory_id=self.memory_id,
                namespace=f"/users/{self.actor_id}/preferences",
                query="What are the user's preferences for menu processing, dietary restrictions, and formatting?",
                top_k=MAX_PREFERENCES,
            )

            # Get user facts
            facts = self.memory_client.retrieve_memories(
                memory_id=self.memory_id,
                namespace=f"/users/{self.actor_id}/facts",
                query="What menus has the user processed? What restaurants and files have they worked with?",
                top_k=MAX_FACTS,
            )

            context_parts = []
            if preferences:
                prefs_text = "\n".join(p["content"]["text"] for p in preferences)
                context_parts.append(f"User Preferences:\n{prefs_text}")
            if facts:
                facts_text = "\n".join(f["content"]["text"] for f in facts)
                context_parts.append(f"User Facts:\n{facts_text}")

            if context_parts:
                context = "\n\n".join(context_parts)
                event.agent.system_prompt += (
                    f"\n\n<user_context>\n{context}\n</user_context>\n\n"
                    "Note: Use this context to personalize responses, but do not "
                    "explicitly mention these preferences or facts unless directly "
                    "relevant to the user's query."
                )
                logger.info("Added user context to system prompt (actor=%s)", self.actor_id)

        except Exception as exc:
            logger.warning("Could not retrieve user context: %s", exc)

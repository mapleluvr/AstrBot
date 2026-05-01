"""Dialogue replay renderer for checkpoint transcript injection.

Converts a list of Message objects into a native multi-role conversation
suitable for injection into the Checkpoint Agent's context.
"""

from astrbot.core.agent.message import Message


class CheckpointRenderer:
    """Renders interaction messages into a transcript for checkpoint generation.

    Uses native multi-role replay (Option A from design doc):
    user/assistant/tool messages are injected as native roles.
    """

    def __init__(self, max_chunk_tokens: int = 6000) -> None:
        self.max_chunk_tokens = max_chunk_tokens

    def render_segments(
        self,
        messages: list[Message],
    ) -> list[dict[str, str]]:
        """Convert a list of Message objects into API-compatible dicts.

        Args:
            messages: Messages to replay.

        Returns:
            List of {"role": str, "content": str} dicts for API injection.
        """
        segments: list[dict[str, str]] = []
        for msg in messages:
            role = self._map_role(msg.role)
            if role is None:
                continue
            content = msg.content or ""
            if not isinstance(content, str):
                content = str(content)
            segments.append({"role": role, "content": content})
        return segments

    def _map_role(self, role: str) -> str | None:
        """Map internal role to API role, or None to skip."""
        role_lower = role.lower()
        if role_lower in ("user", "assistant", "system"):
            return role_lower
        if role_lower in ("tool_call", "tool", "function"):
            return "assistant"
        if role_lower in ("tool_result", "function_result"):
            return "user"
        return None

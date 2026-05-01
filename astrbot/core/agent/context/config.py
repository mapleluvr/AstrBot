from dataclasses import dataclass, field  # noqa: F401
from typing import TYPE_CHECKING

from .compressor import ContextCompressor
from .token_counter import TokenCounter

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider


@dataclass
class ContextConfig:
    """Context configuration class."""

    max_context_tokens: int = 0
    """Maximum number of context tokens. <= 0 means no limit."""
    enforce_max_turns: int = -1
    """Maximum number of conversation turns to keep. -1 means no limit."""
    truncate_turns: int = 1
    """Number of conversation turns to discard at once when truncation is triggered."""
    llm_compress_instruction: str | None = None
    """Instruction prompt for LLM-based compression."""
    llm_compress_keep_recent: int = 0
    """Number of recent messages to keep during LLM-based compression."""
    llm_compress_provider: "Provider | None" = None
    """LLM provider used for compression tasks."""
    custom_token_counter: TokenCounter | None = None
    """Custom token counting method."""
    custom_compressor: ContextCompressor | None = None
    """Custom context compression method."""

    # --- checkpoint fields ---
    context_limit_reached_strategy: str = "truncate_by_turns"
    """Strategy: truncate_by_turns | llm_compress | checkpoint_compress"""
    checkpoint_async_enabled: bool = False
    """Whether async checkpoint is enabled for this context."""
    checkpoint_async_provider: "Provider | None" = None
    """Provider used for checkpoint generation. Falls back to main provider if None."""
    checkpoint_keep_recent: int = 8
    """Number of recent messages to keep as raw tail in checkpoint_compress."""
    checkpoint_max_chunk_tokens: int = 6000
    """Max tokens per transcript chunk."""
    checkpoint_update_interval_turns: int = 8
    """Trigger async checkpoint update every N turns."""
    checkpoint_update_interval_tokens: int = 12000
    """Trigger async checkpoint update every N tokens."""

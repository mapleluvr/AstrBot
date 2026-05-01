"""Async checkpoint scheduler for background checkpoint updates."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider

from astrbot.core.agent.checkpoint.store import CheckpointStore
from astrbot.core.agent.message import Message


class CheckpointScheduler:
    """Schedules and executes async checkpoint updates.

    Triggers checkpoint generation after each agent step based on
    turn/token thresholds. Does NOT block the main agent.
    """

    def __init__(
        self,
        store: CheckpointStore,
        default_provider: Provider | None = None,
        interval_turns: int = 8,
        interval_tokens: int = 12000,
    ) -> None:
        self.store = store
        self.default_provider = default_provider
        self.interval_turns = interval_turns
        self.interval_tokens = interval_tokens
        self._turn_counter: dict[str, int] = {}
        self._token_counter: dict[str, int] = {}
        self._scheduled: set[str] = set()

    def record_step(
        self, owner_type: str, owner_id: str, tokens: int = 0
    ) -> None:
        """Record an agent step for threshold tracking."""
        key = f"{owner_type}:{owner_id}"
        self._turn_counter[key] = self._turn_counter.get(key, 0) + 1
        self._token_counter[key] = self._token_counter.get(key, 0) + tokens

    def should_update(self, owner_type: str, owner_id: str) -> bool:
        """Check if checkpoint update should be triggered."""
        key = f"{owner_type}:{owner_id}"
        if key in self._scheduled:
            return False
        turns = self._turn_counter.get(key, 0)
        tokens = self._token_counter.get(key, 0)
        return (
            turns >= self.interval_turns or tokens >= self.interval_tokens
        )

    async def schedule_update(
        self,
        owner_type: str,
        owner_id: str,
        messages: list[Message],
        provider: Provider | None = None,
    ) -> None:
        """Schedule an async checkpoint update."""
        key = f"{owner_type}:{owner_id}"
        if key in self._scheduled:
            return

        self._scheduled.add(key)
        try:
            prov = provider or self.default_provider
            if prov is None:
                return

            self._turn_counter[key] = 0
            self._token_counter[key] = 0

            latest = await self.store.get_latest(owner_type, owner_id)
            covers_start = (latest.covers_end + 1) if latest else 1

            await self.store.mark_stale(owner_type, owner_id)

            from astrbot.core.agent.checkpoint.schema import (
                build_checkpoint_prompt,
                FINAL_PROMPT_TEMPLATE,
                parse_checkpoint_yaml,
            )
            from astrbot.core.agent.checkpoint.renderer import CheckpointRenderer

            renderer = CheckpointRenderer()
            previous_text = latest.checkpoint_text if latest else None
            version = (latest.version + 1) if latest else 1

            _, pre_messages = build_checkpoint_prompt(
                previous_checkpoint=previous_text,
                version=version,
                start_turn=covers_start,
                end_turn=len(messages),
            )

            prompt_messages: list[Message] = []
            for pm in pre_messages:
                prompt_messages.append(Message(role=pm["role"], content=pm["content"]))

            transcript = renderer.render_segments(messages)
            for seg in transcript:
                prompt_messages.append(Message(role=seg["role"], content=seg["content"]))

            final_prompt = FINAL_PROMPT_TEMPLATE.format(
                start_turn=covers_start,
                end_turn=len(messages),
            )
            prompt_messages.append(Message(role="user", content=final_prompt))

            try:
                response = await prov.text_chat(
                    contexts=prompt_messages,
                    system_prompt=(
                        "You are a state checkpoint recorder. "
                        "Output ONLY the YAML checkpoint, no other text."
                    ),
                )
                checkpoint_text = response.completion_text
                parse_checkpoint_yaml(checkpoint_text)

                await self.store.save(
                    owner_type=owner_type,
                    owner_id=owner_id,
                    covers_start=covers_start,
                    covers_end=len(messages),
                    checkpoint_text=checkpoint_text,
                    provider_id=str(prov.provider_config.get("id", "unknown")),
                    status="ready",
                )
            except Exception:
                await self.store.save(
                    owner_type=owner_type,
                    owner_id=owner_id,
                    covers_start=covers_start,
                    covers_end=len(messages),
                    checkpoint_text=previous_text or "",
                    provider_id=str(prov.provider_config.get("id", "unknown")),
                    status="failed",
                )
        finally:
            self._scheduled.discard(key)

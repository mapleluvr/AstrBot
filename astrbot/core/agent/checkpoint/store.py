"""CheckpointStore for persisting and retrieving state checkpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from astrbot.core.db.po import StateCheckpoint

if TYPE_CHECKING:
    from astrbot.core.db.sqlite import SQLiteDatabase


class CheckpointStore:
    """Manages CRUD operations for StateCheckpoint records."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db

    async def get_latest(
        self, owner_type: str, owner_id: str
    ) -> StateCheckpoint | None:
        """Get the latest ready checkpoint for an owner."""
        async with self.db.get_session() as session:
            stmt = (
                select(StateCheckpoint)
                .where(
                    StateCheckpoint.owner_type == owner_type,
                    StateCheckpoint.owner_id == owner_id,
                    StateCheckpoint.status == "ready",
                )
                .order_by(StateCheckpoint.version.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def save(
        self,
        owner_type: str,
        owner_id: str,
        covers_start: int | None,
        covers_end: int,
        checkpoint_text: str,
        provider_id: str,
        status: str = "ready",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        metadata_json: dict | None = None,
    ) -> StateCheckpoint:
        """Save a new checkpoint, auto-incrementing version."""
        latest = await self.get_latest(owner_type, owner_id)
        next_version = (latest.version + 1) if latest else 1

        checkpoint = StateCheckpoint(
            owner_type=owner_type,
            owner_id=owner_id,
            version=next_version,
            status=status,
            provider_id=provider_id,
            strategy="checkpoint_compress",
            covers_start=covers_start,
            covers_end=covers_end,
            raw_tail_start=covers_end + 1,
            checkpoint_text=checkpoint_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata_json=metadata_json,
        )
        async with self.db.get_session() as session:
            session.add(checkpoint)
            await session.commit()
            await session.refresh(checkpoint)
        return checkpoint

    async def mark_stale(self, owner_type: str, owner_id: str) -> None:
        """Mark all checkpoints for an owner as stale."""
        async with self.db.get_session() as session:
            stmt = (
                select(StateCheckpoint)
                .where(
                    StateCheckpoint.owner_type == owner_type,
                    StateCheckpoint.owner_id == owner_id,
                )
            )
            result = await session.execute(stmt)
            checkpoints = result.scalars().all()
            for cp in checkpoints:
                cp.status = "stale"
            await session.commit()

    async def delete_by_owner(self, owner_type: str, owner_id: str) -> None:
        """Delete all checkpoints for an owner."""
        async with self.db.get_session() as session:
            stmt = (
                select(StateCheckpoint)
                .where(
                    StateCheckpoint.owner_type == owner_type,
                    StateCheckpoint.owner_id == owner_id,
                )
            )
            result = await session.execute(stmt)
            for cp in result.scalars().all():
                await session.delete(cp)
            await session.commit()

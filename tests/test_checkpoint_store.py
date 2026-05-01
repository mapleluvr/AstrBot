"""Tests for CheckpointStore."""

import uuid

import pytest


@pytest.mark.asyncio
async def test_save_and_retrieve_checkpoint():
    from astrbot.core.agent.checkpoint.store import CheckpointStore
    from astrbot.core.db.sqlite import SQLiteDatabase

    db = SQLiteDatabase(":memory:")
    await db.initialize()
    store = CheckpointStore(db)

    owner_id = str(uuid.uuid4())
    cp = await store.save(
        owner_type="conversation",
        owner_id=owner_id,
        covers_start=1,
        covers_end=10,
        checkpoint_text=(
            "checkpoint_version: 1\n"
            "covers:\n"
            "  start_turn: 1\n"
            "  end_turn: 10\n"
        ),
        provider_id="test-provider",
    )
    assert cp.version == 1
    assert cp.status == "ready"
    assert cp.raw_tail_start == 11

    retrieved = await store.get_latest("conversation", owner_id)
    assert retrieved is not None
    assert retrieved.checkpoint_id == cp.checkpoint_id

    cp2 = await store.save(
        owner_type="conversation",
        owner_id=owner_id,
        covers_start=1,
        covers_end=20,
        checkpoint_text="checkpoint_version: 2\n",
        provider_id="test-provider",
    )
    assert cp2.version == 2

    latest = await store.get_latest("conversation", owner_id)
    assert latest is not None
    assert latest.version == 2


@pytest.mark.asyncio
async def test_mark_stale():
    from astrbot.core.agent.checkpoint.store import CheckpointStore
    from astrbot.core.db.sqlite import SQLiteDatabase

    db = SQLiteDatabase(":memory:")
    await db.initialize()
    store = CheckpointStore(db)

    owner_id = str(uuid.uuid4())
    await store.save(
        owner_type="conversation",
        owner_id=owner_id,
        covers_start=1,
        covers_end=10,
        checkpoint_text="v1",
        provider_id="test",
    )
    await store.mark_stale("conversation", owner_id)
    latest = await store.get_latest("conversation", owner_id)
    assert latest is None  # stale checkpoints excluded by get_latest


@pytest.mark.asyncio
async def test_get_latest_returns_none_for_unknown():
    from astrbot.core.agent.checkpoint.store import CheckpointStore
    from astrbot.core.db.sqlite import SQLiteDatabase

    db = SQLiteDatabase(":memory:")
    await db.initialize()
    store = CheckpointStore(db)

    result = await store.get_latest("conversation", "nonexistent")
    assert result is None

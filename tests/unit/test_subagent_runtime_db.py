import pytest

from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_delete_conversation_returns_whether_row_was_deleted(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "astrbot.db"))
    await db.initialize()
    conversation = await db.create_conversation(
        user_id="telegram:FriendMessage:user1",
        platform_id="telegram",
    )

    deleted = await db.delete_conversation(conversation.conversation_id)
    deleted_again = await db.delete_conversation(conversation.conversation_id)

    assert deleted is True
    assert deleted_again is False


@pytest.mark.asyncio
async def test_create_and_get_subagent_instance(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "astrbot.db"))
    await db.initialize()

    instance = await db.create_subagent_instance(
        umo="telegram:FriendMessage:user1",
        scope_type="conversation",
        scope_id="conv-1",
        name="researcher",
        preset_name="research",
        provider_id="provider-a",
        persona_id="persona-a",
        system_prompt="You research things.",
        system_prompt_delta="Focus on citations.",
        tools=["web_search"],
        skills=["summarize"],
        history=[],
        max_persisted_turns=8,
        max_persisted_tokens=4000,
    )

    loaded = await db.get_subagent_instance_by_id(instance.instance_id)

    assert loaded is not None
    assert loaded.name == "researcher"
    assert loaded.scope_type == "conversation"
    assert loaded.provider_id == "provider-a"
    assert loaded.persona_id == "persona-a"
    assert loaded.system_prompt == "You research things."
    assert loaded.system_prompt_delta == "Focus on citations."
    assert loaded.tools == ["web_search"]
    assert loaded.skills == ["summarize"]
    assert loaded.history == []
    assert loaded.max_persisted_turns == 8
    assert loaded.max_persisted_tokens == 4000
    assert loaded.version == 1
    assert loaded.begin_dialogs_injected is False


@pytest.mark.asyncio
async def test_save_subagent_history_rejects_stale_version(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "astrbot.db"))
    await db.initialize()
    instance = await db.create_subagent_instance(
        umo="telegram:FriendMessage:user1",
        scope_type="conversation",
        scope_id="conv-1",
        name="researcher",
        preset_name="research",
        history=[],
    )

    saved = await db.save_subagent_history(
        instance.instance_id,
        history=[{"role": "user", "content": "hello"}],
        token_usage=11,
        begin_dialogs_injected=True,
        expected_version=1,
    )
    stale = await db.save_subagent_history(
        instance.instance_id,
        history=[{"role": "user", "content": "stale"}],
        token_usage=12,
        begin_dialogs_injected=True,
        expected_version=1,
    )

    assert saved is not None
    assert saved.version == 2
    assert stale is None

    loaded = await db.get_subagent_instance_by_id(instance.instance_id)

    assert loaded is not None
    assert loaded.version == 2
    assert loaded.history == [{"role": "user", "content": "hello"}]
    assert loaded.token_usage == 11
    assert loaded.begin_dialogs_injected is True


@pytest.mark.asyncio
async def test_update_subagent_instance_rejects_history_version_fields(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "astrbot.db"))
    await db.initialize()
    instance = await db.create_subagent_instance(
        umo="telegram:FriendMessage:user1",
        scope_type="conversation",
        scope_id="conv-1",
        name="researcher",
        preset_name="research",
        history=[],
    )

    with pytest.raises(ValueError, match="protected subagent instance fields"):
        await db.update_subagent_instance(
            instance.instance_id,
            history=[{"role": "user", "content": "bypass"}],
            version=99,
        )

    loaded = await db.get_subagent_instance_by_id(instance.instance_id)

    assert loaded is not None
    assert loaded.history == []
    assert loaded.version == 1


@pytest.mark.asyncio
async def test_cleanup_subagent_instances_by_scope(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "astrbot.db"))
    await db.initialize()
    umo = "telegram:FriendMessage:user1"
    await db.create_subagent_instance(
        umo=umo,
        scope_type="conversation",
        scope_id="conv-1",
        name="a",
        preset_name="p",
    )
    await db.create_subagent_instance(
        umo=umo,
        scope_type="conversation",
        scope_id="conv-2",
        name="b",
        preset_name="p",
    )
    await db.create_subagent_instance(
        umo=umo,
        scope_type="session",
        scope_id=umo,
        name="c",
        preset_name="p",
    )

    await db.delete_subagent_instances_for_conversation("conv-1")
    remaining = await db.list_subagent_instances(umo=umo)

    assert {item.name for item in remaining} == {"b", "c"}

    await db.delete_subagent_instances_for_session(umo)
    remaining = await db.list_subagent_instances(umo=umo)

    assert remaining == []

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


@pytest.mark.asyncio
async def test_create_and_get_latest_subagent_background_run(tmp_path):
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

    first = await db.create_subagent_background_run(
        instance_id=instance.instance_id,
        umo=instance.umo,
        scope_type=instance.scope_type,
        scope_id=instance.scope_id,
        instance_name=instance.name,
        preset_name=instance.preset_name,
        status="queued",
        input_text="first",
        image_urls=[],
        events=[{"type": "submitted", "message": "first"}],
    )
    second = await db.create_subagent_background_run(
        instance_id=instance.instance_id,
        umo=instance.umo,
        scope_type=instance.scope_type,
        scope_id=instance.scope_id,
        instance_name=instance.name,
        preset_name=instance.preset_name,
        status="running",
        input_text="second",
        image_urls=["https://example.com/a.png"],
        events=[{"type": "submitted", "message": "second"}],
    )

    loaded = await db.get_subagent_background_run(second.task_id)
    latest = await db.get_latest_subagent_background_run(instance.instance_id)

    assert first.task_id != second.task_id
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.input_text == "second"
    assert latest is not None
    assert latest.task_id == second.task_id


@pytest.mark.asyncio
async def test_append_subagent_background_run_event_keeps_recent_entries(tmp_path):
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
    run = await db.create_subagent_background_run(
        instance_id=instance.instance_id,
        umo=instance.umo,
        scope_type=instance.scope_type,
        scope_id=instance.scope_id,
        instance_name=instance.name,
        preset_name=instance.preset_name,
        status="running",
        input_text="collect progress",
        image_urls=[],
        events=[],
    )

    updated = None
    for idx in range(12):
        updated = await db.append_subagent_background_run_event(
            run.task_id,
            {"type": "tool_call", "message": f"tool-{idx}", "tool_name": f"tool_{idx}"},
            max_events=5,
        )

    assert updated is not None
    assert [event["message"] for event in updated.events] == [
        "tool-7",
        "tool-8",
        "tool-9",
        "tool-10",
        "tool-11",
    ]


@pytest.mark.asyncio
async def test_update_subagent_background_run_updates_allowed_runtime_fields(tmp_path):
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
    run = await db.create_subagent_background_run(
        instance_id=instance.instance_id,
        umo=instance.umo,
        scope_type=instance.scope_type,
        scope_id=instance.scope_id,
        instance_name=instance.name,
        preset_name=instance.preset_name,
        status="running",
        input_text="collect progress",
        image_urls=["https://example.com/a.png"],
        events=[],
    )

    updated = await db.update_subagent_background_run(
        run.task_id,
        status="completed",
        final_response="done",
        error_message=None,
        token_usage=42,
    )

    assert updated is not None
    assert updated.status == "completed"
    assert updated.final_response == "done"
    assert updated.token_usage == 42
    assert updated.input_text == "collect progress"
    assert updated.image_urls == ["https://example.com/a.png"]


@pytest.mark.asyncio
async def test_update_subagent_background_run_rejects_provenance_fields(tmp_path):
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
    run = await db.create_subagent_background_run(
        instance_id=instance.instance_id,
        umo=instance.umo,
        scope_type=instance.scope_type,
        scope_id=instance.scope_id,
        instance_name=instance.name,
        preset_name=instance.preset_name,
        status="running",
        input_text="collect progress",
        image_urls=["https://example.com/a.png"],
        events=[],
    )

    with pytest.raises(ValueError, match="protected subagent background run fields"):
        await db.update_subagent_background_run(
            run.task_id,
            instance_id="other-instance",
            input_text="rewritten",
            image_urls=[],
        )

    loaded = await db.get_subagent_background_run(run.task_id)
    assert loaded is not None
    assert loaded.instance_id == instance.instance_id
    assert loaded.input_text == "collect progress"
    assert loaded.image_urls == ["https://example.com/a.png"]

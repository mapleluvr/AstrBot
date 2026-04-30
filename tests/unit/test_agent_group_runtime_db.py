import pytest

from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_agent_group_run_db_round_trip_and_active_workspace_lookup(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "agent-group.db"))
    try:
        await db.initialize()

        run = await db.create_agent_group_run(
            umo="webchat:FriendMessage:user",
            conversation_id="conversation-1",
            workspace_id="workspace-1",
            preset_name="review_team",
            task="Review patch",
            status="active",
            members=[{"name": "planner", "status": "active"}],
            messages=[{"from": "local_agent", "content": "Review patch"}],
            final_opinions={},
            summary=None,
            token_usage={"total": 0},
            metadata={"workspace_path": "data/workspaces/agent_groups/workspace-1"},
        )

        loaded = await db.get_agent_group_run(run.run_id)
        active = await db.get_active_agent_group_run_for_workspace("workspace-1")

        assert loaded.run_id == run.run_id
        assert active.run_id == run.run_id
        assert loaded.members == [{"name": "planner", "status": "active"}]
        assert loaded.messages[0]["content"] == "Review patch"
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_agent_group_run_state_save_uses_optimistic_version(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "agent-group.db"))
    try:
        await db.initialize()
        run = await db.create_agent_group_run(
            umo="webchat:FriendMessage:user",
            conversation_id="conversation-1",
            workspace_id="workspace-1",
            preset_name="review_team",
            task="Review patch",
            status="active",
            members=[],
            messages=[],
            final_opinions={},
            summary=None,
            token_usage={},
            metadata={},
        )

        saved = await db.save_agent_group_state(
            run.run_id,
            status="completed",
            members=[],
            messages=[],
            final_opinions={"planner": "Done"},
            summary="planner: Done",
            token_usage={"total": 1},
            metadata={"done": True},
            expected_version=run.version,
        )
        stale = await db.save_agent_group_state(
            run.run_id,
            status="failed",
            members=[],
            messages=[],
            final_opinions={},
            summary=None,
            token_usage={},
            metadata={},
            expected_version=run.version,
        )

        assert saved is not None
        assert saved.version == run.version + 1
        assert saved.status == "completed"
        assert stale is None
    finally:
        await db.engine.dispose()

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent_group_runtime import AgentGroupRuntimeResult
from astrbot.core.tools.agent_group_tools import (
    ApplyAgentPresetConfigPatchTool,
    CancelAgentGroupTool,
    CreateGroupSubAgentTool,
    DeleteGroupSubAgentTool,
    DraftAgentPresetConfigPatchTool,
    GetAgentGroupStatusTool,
    GetGroupStatusTool,
    ListAgentGroupRunsTool,
    ListAgentGroupPresetsTool,
    MarkCompleteTool,
    MsgToAgentTool,
    ResetGroupSubAgentTool,
    RunGroupSubAgentTool,
    StartAgentGroupTool,
)


class FakeAstrBotConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_config = MagicMock()


def _wrapper(runtime_manager) -> ContextWrapper:
    plugin_context = MagicMock()
    plugin_context.agent_group_runtime_manager = runtime_manager
    event = MagicMock()
    event.unified_msg_origin = "platform:private:user"
    return ContextWrapper(context=SimpleNamespace(context=plugin_context, event=event))


def _config_wrapper(config, *, runtime_manager=None) -> ContextWrapper:
    wrapper = _wrapper(runtime_manager or MagicMock())
    wrapper.context.context.astrbot_config = config
    wrapper.context.context.subagent_orchestrator = MagicMock()
    wrapper.context.context.subagent_runtime_manager = MagicMock()
    return wrapper


def _member_wrapper(runtime_manager, *, run_id="run-1", member_name="planner") -> ContextWrapper:
    wrapper = _wrapper(runtime_manager)
    wrapper.context.event.get_extra.side_effect = lambda key: (
        {"run_id": run_id, "member_name": member_name}
        if key == "agent_group_member_context"
        else None
    )
    return wrapper


def _payload(result: str) -> dict:
    return json.loads(result)


@pytest.mark.asyncio
async def test_list_agent_group_presets_returns_structured_payload():
    manager = MagicMock()
    manager.list_presets.return_value = [
        SimpleNamespace(
            name="review_team",
            members=[
                SimpleNamespace(
                    name="planner",
                    source_type="subagent",
                    subagent_preset="planner_preset",
                    persona_id="",
                )
            ],
            initial_recipients=["planner"],
            principles=["Be concise"],
            collaboration_prompt="Work together",
            summary_preset="agent_group_summary",
            summary_include_private=False,
            token_limit=None,
            time_limit_seconds=600,
        )
    ]

    result = _payload(await ListAgentGroupPresetsTool().call(_wrapper(manager)))

    assert result["ok"] is True
    assert result["data"]["presets"][0]["name"] == "review_team"
    assert result["data"]["presets"][0]["members"][0]["subagent_preset"] == (
        "planner_preset"
    )
    assert result["data"]["presets"][0]["members"][0]["source_type"] == "subagent"
    assert "workspace_id" not in result["data"]["presets"][0]
    assert "role" not in result["data"]["presets"][0]["members"][0]
    assert "tools" not in result["data"]["presets"][0]["members"][0]
    assert "skills" not in result["data"]["presets"][0]["members"][0]


@pytest.mark.asyncio
async def test_start_agent_group_returns_run_id_and_calls_manager():
    manager = MagicMock()
    manager.start_run = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(
            {"run_id": "run-1", "status": "active"}
        )
    )

    result = _payload(
        await StartAgentGroupTool().call(
            _wrapper(manager),
            preset_name="review_team",
            task="Review this",
            workspace_id="workspace-1",
        )
    )

    assert result == {
        "ok": True,
        "data": {"run_id": "run-1", "status": "active"},
        "error": None,
    }
    manager.start_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_cancel_and_member_tools_return_runtime_errors():
    manager = MagicMock()
    manager.get_status = AsyncMock(
        return_value=AgentGroupRuntimeResult.failure(
            "run_not_found",
            "Agent group run was not found.",
        )
    )
    manager.cancel_run = AsyncMock(
        return_value=AgentGroupRuntimeResult.failure(
            "run_not_found",
            "Agent group run was not found.",
        )
    )
    manager.msg_to_agent = AsyncMock(
        return_value=AgentGroupRuntimeResult.failure(
            "member_not_found",
            "Agent group member was not found.",
        )
    )
    manager.mark_complete = AsyncMock(
        return_value=AgentGroupRuntimeResult.failure(
            "member_not_found",
            "Agent group member was not found.",
        )
    )

    status = _payload(
        await GetAgentGroupStatusTool().call(_wrapper(manager), run_id="missing")
    )
    cancel = _payload(
        await CancelAgentGroupTool().call(_wrapper(manager), run_id="missing")
    )
    msg = _payload(
        await MsgToAgentTool().call(
            _member_wrapper(manager),
            run_id="run-1",
            to_member="missing",
            content="hello",
        )
    )
    done = _payload(
        await MarkCompleteTool().call(
            _member_wrapper(manager, member_name="missing"),
            run_id="run-1",
            final_opinion="done",
        )
    )

    assert status["error"]["error_code"] == "run_not_found"
    assert cancel["error"]["error_code"] == "run_not_found"
    assert msg["error"]["error_code"] == "member_not_found"
    assert done["error"]["error_code"] == "member_not_found"


@pytest.mark.asyncio
async def test_get_agent_group_status_forwards_private_visibility_option():
    manager = MagicMock()
    manager.get_status = AsyncMock(
        return_value=AgentGroupRuntimeResult.success({"run_id": "run-1"})
    )

    result = _payload(
        await GetAgentGroupStatusTool().call(
            _wrapper(manager),
            run_id="run-1",
            include_private=True,
        )
    )

    assert result["ok"] is True
    manager.get_status.assert_awaited_once_with(
        run_id="run-1",
        workspace_id=None,
        include_private=True,
    )


@pytest.mark.asyncio
async def test_list_agent_group_runs_forwards_filters_and_privacy_option():
    manager = MagicMock()
    manager.list_runs = AsyncMock(
        return_value=AgentGroupRuntimeResult.success({"runs": []})
    )

    result = _payload(
        await ListAgentGroupRunsTool().call(
            _wrapper(manager),
            workspace_id="code-review",
            status="completed",
            include_private=True,
        )
    )

    assert result["ok"] is True
    manager.list_runs.assert_awaited_once_with(
        umo=None,
        workspace_id="code-review",
        status="completed",
        include_private=True,
    )


@pytest.mark.asyncio
async def test_tool_returns_structured_error_when_manager_missing():
    result = _payload(await ListAgentGroupPresetsTool().call(_wrapper(None)))

    assert result == {
        "ok": False,
        "data": None,
        "error": {
            "error_code": "agent_group_runtime_unavailable",
            "message": "Agent group runtime manager is not available.",
            "details": None,
        },
    }


@pytest.mark.asyncio
async def test_local_agent_tools_reject_group_member_context():
    manager = MagicMock()
    manager.start_run = AsyncMock()

    result = _payload(
        await StartAgentGroupTool().call(
            _member_wrapper(manager),
            preset_name="review_team",
            task="Review this",
        )
    )

    assert result["ok"] is False
    assert (
        result["error"]["error_code"]
        == "agent_group_local_tool_forbidden_in_member_context"
    )
    manager.start_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_agent_preset_config_patch_does_not_save_config():
    config = FakeAstrBotConfig(
        {
            "agent_group": {
                "summary_preset": "agent_group_summary",
                "presets": [{"name": "old_team", "future": {"keep": True}}],
            }
        }
    )
    wrapper = _config_wrapper(config)
    proposed = {
        "summary_preset": "agent_group_summary",
        "presets": [{"name": "new_team"}],
        "future_top_level": "kept",
    }

    result = _payload(
        await DraftAgentPresetConfigPatchTool().call(
            wrapper,
            section="agent_group",
            config=proposed,
            reason="Create a smaller review team",
        )
    )

    assert result["ok"] is True
    assert result["data"]["requires_confirmation"] is True
    assert result["data"]["section"] == "agent_group"
    assert result["data"]["before"]["presets"][0]["name"] == "old_team"
    assert result["data"]["after"]["presets"][0]["name"] == "new_team"
    assert result["data"]["after"]["future_top_level"] == "kept"
    config.save_config.assert_not_called()


@pytest.mark.asyncio
async def test_apply_agent_preset_config_patch_requires_confirmation():
    config = FakeAstrBotConfig({"agent_group": {"presets": []}})
    wrapper = _config_wrapper(config)
    draft = _payload(
        await DraftAgentPresetConfigPatchTool().call(
            wrapper,
            section="agent_group",
            config={
                "presets": [
                    {
                        "name": "new_team",
                        "workspace_id": "legacy",
                        "members": [
                            {
                                "name": "planner",
                                "source_type": "subagent",
                                "subagent_preset": "planner",
                                "role": "legacy role",
                                "tools": ["legacy_tool"],
                                "skills": ["legacy_skill"],
                            },
                            {
                                "name": "reviewer",
                                "source_type": "persona",
                                "persona_id": "default",
                            },
                        ],
                    }
                ]
            },
        )
    )

    result = _payload(
        await ApplyAgentPresetConfigPatchTool().call(
            wrapper,
            draft_id=draft["data"]["draft_id"],
            confirmed=False,
        )
    )

    assert result["ok"] is False
    assert result["error"]["error_code"] == "confirmation_required"
    config.save_config.assert_not_called()


@pytest.mark.asyncio
async def test_apply_agent_group_preset_config_patch_saves_and_reloads_runtime():
    runtime_manager = MagicMock()
    config = FakeAstrBotConfig({"agent_group": {"presets": []}})
    wrapper = _config_wrapper(config, runtime_manager=runtime_manager)
    draft = _payload(
        await DraftAgentPresetConfigPatchTool().call(
            wrapper,
            section="agent_group",
            config={
                "presets": [
                    {
                        "name": "new_team",
                        "workspace_id": "legacy",
                        "members": [
                            {
                                "name": "planner",
                                "source_type": "subagent",
                                "subagent_preset": "planner",
                                "role": "legacy role",
                                "tools": ["legacy_tool"],
                                "skills": ["legacy_skill"],
                            },
                            {
                                "name": "reviewer",
                                "source_type": "persona",
                                "persona_id": "default",
                            },
                        ],
                    }
                ]
            },
        )
    )

    result = _payload(
        await ApplyAgentPresetConfigPatchTool().call(
            wrapper,
            draft_id=draft["data"]["draft_id"],
            confirmed=True,
        )
    )

    assert result["ok"] is True
    assert config["agent_group"]["presets"][0]["name"] == "new_team"
    assert "workspace_id" not in config["agent_group"]["presets"][0]
    assert config["agent_group"]["presets"][0]["members"] == [
        {
            "name": "planner",
            "source_type": "subagent",
            "subagent_preset": "planner",
            "persona_id": "",
            "enabled": True,
        },
        {
            "name": "reviewer",
            "source_type": "persona",
            "subagent_preset": "",
            "persona_id": "default",
            "enabled": True,
        },
    ]
    config.save_config.assert_called_once()
    runtime_manager.reload_from_config.assert_called_once_with(config["agent_group"])


@pytest.mark.asyncio
async def test_apply_subagent_preset_config_patch_saves_and_reloads_managers():
    config = FakeAstrBotConfig({"subagent_orchestrator": {"agents": []}})
    wrapper = _config_wrapper(config)
    draft = _payload(
        await DraftAgentPresetConfigPatchTool().call(
            wrapper,
            section="subagent_orchestrator",
            config={"agents": [{"name": "writer"}], "future_field": "kept"},
        )
    )

    result = _payload(
        await ApplyAgentPresetConfigPatchTool().call(
            wrapper,
            draft_id=draft["data"]["draft_id"],
            confirmed=True,
        )
    )

    assert result["ok"] is True
    assert config["subagent_orchestrator"]["agents"][0]["name"] == "writer"
    assert config["subagent_orchestrator"]["agents"][0]["provider_id"] is None
    assert any(
        agent.get("name") == "agent_group_summary"
        for agent in config["subagent_orchestrator"]["agents"]
    )
    assert config["subagent_orchestrator"]["future_field"] == "kept"
    config.save_config.assert_called_once()
    wrapper.context.context.subagent_orchestrator.reload_from_config.assert_called_once_with(
        config["subagent_orchestrator"]
    )
    wrapper.context.context.subagent_runtime_manager.reload_from_config.assert_called_once_with(
        config["subagent_orchestrator"]
    )


@pytest.mark.asyncio
async def test_apply_agent_preset_config_patch_rejects_stale_config():
    config = FakeAstrBotConfig({"agent_group": {"presets": []}})
    wrapper = _config_wrapper(config)
    draft = _payload(
        await DraftAgentPresetConfigPatchTool().call(
            wrapper,
            section="agent_group",
            config={"presets": [{"name": "new_team"}]},
        )
    )
    config["agent_group"] = {"presets": [{"name": "changed_elsewhere"}]}

    result = _payload(
        await ApplyAgentPresetConfigPatchTool().call(
            wrapper,
            draft_id=draft["data"]["draft_id"],
            confirmed=True,
        )
    )

    assert result["ok"] is False
    assert result["error"]["error_code"] == "stale_config"
    config.save_config.assert_not_called()


@pytest.mark.asyncio
async def test_member_tools_require_injected_member_context():
    manager = MagicMock()
    manager.mark_complete = AsyncMock()

    result = _payload(
        await MarkCompleteTool().call(
            _wrapper(manager),
            run_id="run-1",
            member_name="planner",
            final_opinion="done",
        )
    )

    assert result["ok"] is False
    assert result["error"]["error_code"] == "agent_group_member_context_required"
    manager.mark_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_tool_uses_context_identity_and_rejects_mismatch():
    manager = MagicMock()
    manager.mark_complete = AsyncMock(
        return_value=AgentGroupRuntimeResult.success({"status": "active"})
    )
    wrapper = _member_wrapper(manager)

    success = _payload(
        await MarkCompleteTool().call(
            wrapper,
            run_id="run-1",
            final_opinion="done",
        )
    )
    mismatch = _payload(
        await MarkCompleteTool().call(
            _member_wrapper(manager),
            run_id="run-1",
            member_name="reviewer",
            final_opinion="done",
        )
    )

    assert success["ok"] is True
    manager.mark_complete.assert_awaited_once_with(
        "run-1",
        "planner",
        "done",
        actor_member="planner",
        event=wrapper.context.event,
        runtime_context=wrapper.context.context,
    )
    assert mismatch["ok"] is False
    assert mismatch["error"]["error_code"] == "agent_group_member_context_mismatch"


@pytest.mark.asyncio
async def test_get_group_status_validates_member_run_context():
    manager = MagicMock()
    manager.get_status = AsyncMock(
        return_value=AgentGroupRuntimeResult.success({"run_id": "run-1"})
    )

    success = _payload(
        await GetGroupStatusTool().call(
            _member_wrapper(manager),
            run_id="run-1",
        )
    )
    mismatch = _payload(
        await GetGroupStatusTool().call(
            _member_wrapper(manager),
            run_id="run-2",
        )
    )

    assert success["ok"] is True
    manager.get_status.assert_awaited_once_with(run_id="run-1")
    assert mismatch["ok"] is False
    assert mismatch["error"]["error_code"] == "agent_group_member_context_mismatch"


@pytest.mark.asyncio
async def test_group_subagent_tools_use_member_context_identity():
    manager = MagicMock()
    manager.create_helper_subagent = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(
            {"helper": {"helper_name": "analysis"}}
        )
    )
    manager.run_helper_subagent = AsyncMock(
        return_value=AgentGroupRuntimeResult.success({"final_response": "done"})
    )
    manager.reset_helper_subagent = AsyncMock(
        return_value=AgentGroupRuntimeResult.success({"helper": {}})
    )
    manager.delete_helper_subagent = AsyncMock(
        return_value=AgentGroupRuntimeResult.success({"helper": {}})
    )
    wrapper = _member_wrapper(manager)

    create = _payload(
        await CreateGroupSubAgentTool().call(
            wrapper,
            helper_name="analysis",
            preset_name="researcher",
        )
    )
    run = _payload(
        await RunGroupSubAgentTool().call(
            wrapper,
            helper_name="analysis",
            input_text="inspect this",
        )
    )
    reset = _payload(
        await ResetGroupSubAgentTool().call(
            wrapper,
            helper_name="analysis",
        )
    )
    delete = _payload(
        await DeleteGroupSubAgentTool().call(
            wrapper,
            helper_name="analysis",
        )
    )

    assert create["ok"] is True
    assert run["ok"] is True
    assert reset["ok"] is True
    assert delete["ok"] is True
    manager.create_helper_subagent.assert_awaited_once_with(
        "run-1",
        from_member="planner",
        helper_name="analysis",
        preset_name="researcher",
        actor_member="planner",
        event=wrapper.context.event,
        runtime_context=wrapper.context.context,
    )
    manager.run_helper_subagent.assert_awaited_once_with(
        "run-1",
        from_member="planner",
        helper_name="analysis",
        input_text="inspect this",
        actor_member="planner",
        event=wrapper.context.event,
        runtime_context=wrapper.context.context,
    )
    manager.reset_helper_subagent.assert_awaited_once_with(
        "run-1",
        from_member="planner",
        helper_name="analysis",
        actor_member="planner",
        event=wrapper.context.event,
    )
    manager.delete_helper_subagent.assert_awaited_once_with(
        "run-1",
        from_member="planner",
        helper_name="analysis",
        actor_member="planner",
        event=wrapper.context.event,
        runtime_context=wrapper.context.context,
    )

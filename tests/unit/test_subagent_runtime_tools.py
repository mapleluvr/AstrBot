"""Tests for persistent sub-agent runtime tools."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.subagent_runtime import SubAgentRuntimeResult
from astrbot.core.tools.subagent_runtime_tools import (
    CreateSubAgentTool,
    DeleteSubAgentTool,
    ListSubAgentPresetsTool,
    ListSubAgentsTool,
    ResetSubAgentTool,
    RunSubAgentTool,
    UpdateSubAgentTool,
)


def _wrapper(runtime_manager) -> ContextWrapper:
    plugin_context = MagicMock()
    plugin_context.subagent_runtime_manager = runtime_manager
    event = MagicMock()
    event.unified_msg_origin = "platform:private:user"
    return ContextWrapper(context=SimpleNamespace(context=plugin_context, event=event))


def _payload(result: str) -> dict:
    return json.loads(result)


@pytest.mark.asyncio
async def test_list_subagent_presets_returns_structured_success():
    manager = MagicMock()
    manager.list_presets.return_value = [
        SimpleNamespace(
            name="researcher",
            runtime_mode="persistent",
            public_description="Research things",
            provider_id="provider-a",
            persona_id="persona-a",
            tools=["search"],
            skills=["summarize"],
        )
    ]

    result = _payload(await ListSubAgentPresetsTool().call(_wrapper(manager)))

    assert result == {
        "ok": True,
        "data": {
            "presets": [
                {
                    "name": "researcher",
                    "runtime_mode": "persistent",
                    "public_description": "Research things",
                    "provider_id": "provider-a",
                    "persona_id": "persona-a",
                    "tools": ["search"],
                    "skills": ["summarize"],
                }
            ]
        },
        "error": None,
    }
    manager.list_presets.assert_called_once_with(runtime_mode="persistent")


@pytest.mark.asyncio
async def test_create_subagent_returns_structured_success():
    instance = SimpleNamespace(
        instance_id="inst-1",
        name="analyst",
        preset_name="researcher",
        scope_type="conversation",
        scope_id="conv-1",
    )
    manager = MagicMock()
    manager.create_instance = AsyncMock(
        return_value=SubAgentRuntimeResult.success(instance)
    )

    result = _payload(
        await CreateSubAgentTool().call(
            _wrapper(manager),
            name="analyst",
            preset_name="researcher",
            scope_type="conversation",
            overrides={"tools": ["search"]},
        )
    )

    assert result["ok"] is True
    assert result["data"]["created"] is True
    assert result["data"]["instance"] == {
        "instance_id": "inst-1",
        "name": "analyst",
        "preset_name": "researcher",
        "scope_type": "conversation",
        "scope_id": "conv-1",
    }
    manager.create_instance.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_subagents_returns_structured_success():
    manager = MagicMock()
    manager.list_instances = AsyncMock(
        return_value=[
            SimpleNamespace(
                instance_id="inst-1",
                name="analyst",
                preset_name="researcher",
                scope_type="session",
                scope_id="platform:private:user",
            )
        ]
    )

    result = _payload(await ListSubAgentsTool().call(_wrapper(manager)))

    assert result == {
        "ok": True,
        "data": {
            "instances": [
                {
                    "instance_id": "inst-1",
                    "name": "analyst",
                    "preset_name": "researcher",
                    "scope_type": "session",
                    "scope_id": "platform:private:user",
                }
            ]
        },
        "error": None,
    }


@pytest.mark.asyncio
async def test_update_subagent_returns_structured_success():
    instance = SimpleNamespace(
        instance_id="inst-1",
        name="analyst",
        preset_name="researcher",
        scope_type="conversation",
        scope_id="conv-1",
    )
    manager = MagicMock()
    manager.update_instance = AsyncMock(
        return_value=SubAgentRuntimeResult.success(instance)
    )

    result = _payload(
        await UpdateSubAgentTool().call(
            _wrapper(manager),
            name="analyst",
            scope_type="conversation",
            updates={"provider_id": "provider-b"},
        )
    )

    assert result["ok"] is True
    assert result["data"]["updated"] is True
    assert result["data"]["instance"]["instance_id"] == "inst-1"


@pytest.mark.asyncio
async def test_reset_subagent_returns_structured_success():
    instance = SimpleNamespace(
        instance_id="inst-1",
        name="analyst",
        preset_name="researcher",
        scope_type="conversation",
        scope_id="conv-1",
    )
    manager = MagicMock()
    manager.reset_instance = AsyncMock(
        return_value=SubAgentRuntimeResult.success(instance)
    )

    result = _payload(
        await ResetSubAgentTool().call(
            _wrapper(manager),
            name="analyst",
            scope_type="conversation",
        )
    )

    assert result["ok"] is True
    assert result["data"]["reset"] is True
    assert result["data"]["instance"]["name"] == "analyst"


@pytest.mark.asyncio
async def test_run_subagent_returns_structured_error_when_runtime_manager_missing():
    result = _payload(
        await RunSubAgentTool().call(
            _wrapper(None),
            name="analyst",
            input="summarize this",
        )
    )

    assert result == {
        "ok": False,
        "data": None,
        "error": {
            "error_code": "subagent_runtime_unavailable",
            "message": "Sub-agent runtime manager is not available.",
            "details": None,
        },
    }


@pytest.mark.asyncio
async def test_run_subagent_returns_structured_success():
    manager = MagicMock()
    manager.run_instance = AsyncMock(
        return_value=SubAgentRuntimeResult.success(
            {
                "final_response": "analysis complete",
                "metadata": {
                    "instance_id": "inst-1",
                    "name": "analyst",
                    "version": 2,
                },
            }
        )
    )

    result = _payload(
        await RunSubAgentTool().call(
            _wrapper(manager),
            name="analyst",
            input="summarize this",
            image_urls=["https://example.com/a.png"],
            scope_type="conversation",
        )
    )

    assert result == {
        "ok": True,
        "data": {
            "final_response": "analysis complete",
            "metadata": {
                "instance_id": "inst-1",
                "name": "analyst",
                "version": 2,
            },
        },
        "error": None,
    }
    manager.run_instance.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_subagent_returns_structured_runtime_error():
    manager = MagicMock()
    manager.run_instance = AsyncMock(
        return_value=SubAgentRuntimeResult.failure(
            "subagent_execution_failed",
            "Sub-agent execution failed.",
            {"error": "provider unavailable"},
        )
    )

    result = _payload(
        await RunSubAgentTool().call(
            _wrapper(manager),
            name="analyst",
            input="summarize this",
        )
    )

    assert result == {
        "ok": False,
        "data": None,
        "error": {
            "error_code": "subagent_execution_failed",
            "message": "Sub-agent execution failed.",
            "details": {"error": "provider unavailable"},
        },
    }


@pytest.mark.asyncio
async def test_delete_subagent_returns_structured_success():
    manager = MagicMock()
    manager.delete_instance = AsyncMock(
        return_value=SubAgentRuntimeResult.success(
            {
                "instance_id": "inst-1",
                "name": "analyst",
                "preset_name": "researcher",
                "scope_type": "conversation",
                "scope_id": "conv-1",
            }
        )
    )

    result = _payload(
        await DeleteSubAgentTool().call(
            _wrapper(manager),
            name="analyst",
            scope_type="conversation",
        )
    )

    assert result["ok"] is True
    assert result["data"]["deleted"] is True
    assert result["data"]["instance"]["instance_id"] == "inst-1"


@pytest.mark.asyncio
async def test_tool_returns_structured_error_when_runtime_manager_missing():
    result = _payload(await ListSubAgentsTool().call(_wrapper(None)))

    assert result == {
        "ok": False,
        "data": None,
        "error": {
            "error_code": "subagent_runtime_unavailable",
            "message": "Sub-agent runtime manager is not available.",
            "details": None,
        },
    }


@pytest.mark.asyncio
async def test_create_subagent_returns_structured_runtime_error():
    manager = MagicMock()
    manager.create_instance = AsyncMock(
        return_value=SubAgentRuntimeResult.failure(
            "preset_not_found",
            "Persistent sub-agent preset was not found.",
        )
    )

    result = _payload(
        await CreateSubAgentTool().call(
            _wrapper(manager), name="analyst", preset_name="missing"
        )
    )

    assert result == {
        "ok": False,
        "data": None,
        "error": {
            "error_code": "preset_not_found",
            "message": "Persistent sub-agent preset was not found.",
            "details": None,
        },
    }

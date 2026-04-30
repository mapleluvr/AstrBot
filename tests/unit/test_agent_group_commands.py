from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.builtin_stars.builtin_commands.commands.agent_group import (
    AgentGroupCommands,
)
from astrbot.core.agent_group_runtime import AgentGroupRuntimeResult


class FakeEvent:
    unified_msg_origin = "webchat:FriendMessage:user"

    def __init__(self):
        self.result = None

    def set_result(self, result):
        self.result = result


def _message_text(event: FakeEvent) -> str:
    return event.result.chain[0].text


@pytest.mark.asyncio
async def test_agent_group_start_command_returns_run_id():
    runtime_manager = SimpleNamespace(
        start_run=AsyncMock(
            return_value=AgentGroupRuntimeResult.success(
                {"run_id": "run-1", "status": "active"}
            )
        )
    )
    commands = AgentGroupCommands(SimpleNamespace(agent_group_runtime_manager=runtime_manager))
    event = FakeEvent()

    await commands.agent_group(event, "start", "review_team", "review", "this")

    assert "run-1" in _message_text(event)
    runtime_manager.start_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_group_status_command_reports_missing_runtime():
    commands = AgentGroupCommands(SimpleNamespace(agent_group_runtime_manager=None))
    event = FakeEvent()

    await commands.agent_group(event, "status", "run-1")

    assert "runtime manager is not available" in _message_text(event)


@pytest.mark.asyncio
async def test_agent_group_cancel_command_reports_runtime_error():
    runtime_manager = SimpleNamespace(
        cancel_run=AsyncMock(
            return_value=AgentGroupRuntimeResult.failure(
                "run_not_found",
                "Agent group run was not found.",
            )
        )
    )
    commands = AgentGroupCommands(SimpleNamespace(agent_group_runtime_manager=runtime_manager))
    event = FakeEvent()

    await commands.agent_group(event, "cancel", "missing")

    assert "run_not_found" in _message_text(event)

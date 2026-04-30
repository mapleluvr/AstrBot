from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent_group_runtime import AgentGroupRuntimeResult, VERSION_CONFLICT
from astrbot.core.tools.computer_tools.fs import FileEditTool, FileReadTool, FileWriteTool
from astrbot.core.tools.computer_tools.shell import ExecuteShellTool


class FakeLease:
    def __init__(self):
        self.released = False

    async def release(self):
        self.released = True


class FakeFileSystem:
    def __init__(self):
        self.edit_calls = []
        self.write_calls = []

    async def edit_file(self, **kwargs):
        self.edit_calls.append(kwargs)
        return {"success": True, "replacements": 1}

    async def write_file(self, **kwargs):
        self.write_calls.append(kwargs)
        return {"success": True}


class FakeShell:
    def __init__(self):
        self.exec_calls = []

    async def exec(self, command, **kwargs):
        self.exec_calls.append({"command": command, **kwargs})
        return {"success": True, "stdout": "ok", "stderr": "", "exit_code": 0}


def _wrapper(manager, *, helper=False):
    plugin_context = MagicMock()
    plugin_context.agent_group_runtime_manager = manager
    plugin_context.get_config.return_value = {
        "provider_settings": {
            "computer_use_runtime": "local",
            "computer_use_require_admin": False,
        }
    }
    event = MagicMock()
    event.unified_msg_origin = "platform:private:user"
    event.role = "member"
    event.get_extra.side_effect = lambda key: (
        {"run_id": "run-1", "creator_member": "planner", "helper_name": "analysis"}
        if helper and key == "agent_group_helper_context"
        else (
            {"run_id": "run-1", "member_name": "planner"}
            if not helper and key == "agent_group_member_context"
            else None
        )
    )
    return ContextWrapper(context=SimpleNamespace(context=plugin_context, event=event))


@pytest.mark.asyncio
async def test_agent_group_file_read_records_member_workspace_version(monkeypatch):
    manager = MagicMock()
    manager.resolve_workspace_file_path = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(
            {"path": "C:/workspace/notes.txt", "relative_path": "notes.txt"}
        )
    )
    manager.record_workspace_file_read = AsyncMock(
        return_value=AgentGroupRuntimeResult.success()
    )
    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.fs.get_booter",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.fs.read_file_tool_result",
        AsyncMock(return_value="file content"),
    )

    result = await FileReadTool().call(_wrapper(manager), "notes.txt")

    assert result == "file content"
    manager.resolve_workspace_file_path.assert_awaited_once_with(
        "run-1",
        "planner",
        "notes.txt",
    )
    manager.record_workspace_file_read.assert_awaited_once_with(
        "run-1",
        "planner",
        "C:/workspace/notes.txt",
    )


@pytest.mark.asyncio
async def test_agent_group_file_write_uses_workspace_lock_and_updates_version(
    monkeypatch,
):
    lease = FakeLease()
    fs = FakeFileSystem()
    manager = MagicMock()
    manager.resolve_workspace_file_path = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(
            {"path": "C:/workspace/notes.txt", "relative_path": "notes.txt"}
        )
    )
    manager.acquire_workspace_write_lock = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(lease)
    )
    manager.record_workspace_file_write = AsyncMock(
        return_value=AgentGroupRuntimeResult.success()
    )
    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.fs.get_booter",
        AsyncMock(return_value=SimpleNamespace(fs=fs)),
    )

    result = await FileWriteTool().call(_wrapper(manager, helper=True), "notes.txt", "hi")

    assert result == "File written successfully: C:/workspace/notes.txt"
    manager.resolve_workspace_file_path.assert_awaited_once_with(
        "run-1",
        "planner",
        "notes.txt",
    )
    manager.acquire_workspace_write_lock.assert_awaited_once_with(
        "run-1",
        "planner",
        paths=["C:/workspace/notes.txt"],
    )
    manager.record_workspace_file_write.assert_awaited_once_with(
        "run-1",
        "planner",
        ["C:/workspace/notes.txt"],
    )
    assert fs.write_calls[0]["path"] == "C:/workspace/notes.txt"
    assert lease.released is True


@pytest.mark.asyncio
async def test_agent_group_file_write_returns_version_conflict_without_writing(
    monkeypatch,
):
    fs = FakeFileSystem()
    manager = MagicMock()
    manager.resolve_workspace_file_path = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(
            {"path": "C:/workspace/notes.txt", "relative_path": "notes.txt"}
        )
    )
    manager.acquire_workspace_write_lock = AsyncMock(
        return_value=AgentGroupRuntimeResult.failure(
            VERSION_CONFLICT,
            "Workspace file changed after this member last read it.",
            {"path": "notes.txt"},
        )
    )
    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.fs.get_booter",
        AsyncMock(return_value=SimpleNamespace(fs=fs)),
    )

    result = await FileWriteTool().call(_wrapper(manager), "notes.txt", "hi")

    assert "version_conflict" in result
    assert fs.write_calls == []


@pytest.mark.asyncio
async def test_agent_group_file_edit_uses_workspace_lock_and_updates_version(
    monkeypatch,
):
    lease = FakeLease()
    fs = FakeFileSystem()
    manager = MagicMock()
    manager.resolve_workspace_file_path = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(
            {"path": "C:/workspace/notes.txt", "relative_path": "notes.txt"}
        )
    )
    manager.acquire_workspace_write_lock = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(lease)
    )
    manager.record_workspace_file_write = AsyncMock(
        return_value=AgentGroupRuntimeResult.success()
    )
    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.fs.get_booter",
        AsyncMock(return_value=SimpleNamespace(fs=fs)),
    )

    result = await FileEditTool().call(_wrapper(manager), "notes.txt", "old", "new")

    assert result == (
        "Edited C:/workspace/notes.txt. "
        "Replaced 1 occurrence(s) using first match mode."
    )
    manager.acquire_workspace_write_lock.assert_awaited_once_with(
        "run-1",
        "planner",
        paths=["C:/workspace/notes.txt"],
    )
    manager.record_workspace_file_write.assert_awaited_once_with(
        "run-1",
        "planner",
        ["C:/workspace/notes.txt"],
    )
    assert fs.edit_calls[0]["path"] == "C:/workspace/notes.txt"
    assert lease.released is True


@pytest.mark.asyncio
async def test_agent_group_shell_uses_workspace_write_lock_and_group_cwd(monkeypatch):
    lease = FakeLease()
    shell = FakeShell()
    manager = MagicMock()
    manager.resolve_workspace_file_path = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(
            {"path": "C:/workspace", "relative_path": "."}
        )
    )
    manager.acquire_workspace_write_lock = AsyncMock(
        return_value=AgentGroupRuntimeResult.success(lease)
    )
    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.shell.get_booter",
        AsyncMock(return_value=SimpleNamespace(shell=shell)),
    )

    result = await ExecuteShellTool().call(_wrapper(manager), "dir")

    assert '"stdout": "ok"' in result
    manager.acquire_workspace_write_lock.assert_awaited_once_with(
        "run-1",
        "planner",
    )
    assert shell.exec_calls[0]["cwd"] == "C:/workspace"
    assert lease.released is True

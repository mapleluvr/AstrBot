import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.computer.computer_client import get_booter

from ..registry import builtin_tool
from .util import check_admin_permission, is_local_runtime, workspace_root

_COMPUTER_RUNTIME_TOOL_CONFIG = {
    "provider_settings.computer_use_runtime": ("local", "sandbox"),
}


def _agent_group_workspace_context(
    context: ContextWrapper[AstrAgentContext],
) -> tuple[Any, str, str] | None:
    event = context.context.event
    if not hasattr(event, "get_extra"):
        return None
    member_context = event.get_extra("agent_group_member_context")
    helper_context = event.get_extra("agent_group_helper_context")
    run_id = None
    member_name = None
    if isinstance(member_context, dict):
        run_id = member_context.get("run_id")
        member_name = member_context.get("member_name")
    if not run_id and isinstance(helper_context, dict):
        run_id = helper_context.get("run_id")
        member_name = helper_context.get("creator_member")
    if not run_id or not member_name:
        return None
    manager = getattr(context.context.context, "agent_group_runtime_manager", None)
    if manager is None:
        return None
    return manager, str(run_id), str(member_name)


def _agent_group_error_text(result: Any) -> str:
    error = getattr(result, "error", None)
    if error is None:
        return "agent_group_workspace_error: Agent group workspace operation failed."
    return f"{error.error_code}: {error.message}"


async def _agent_group_workspace_cwd(
    context: ContextWrapper[AstrAgentContext],
) -> tuple[str | None, str | None]:
    workspace_context = _agent_group_workspace_context(context)
    if workspace_context is None:
        return None, None
    manager, run_id, member_name = workspace_context
    result = await manager.resolve_workspace_file_path(run_id, member_name, ".")
    if not getattr(result, "ok", False):
        return None, _agent_group_error_text(result)
    return str(result.data["path"]), None


async def _acquire_agent_group_write_lease(
    context: ContextWrapper[AstrAgentContext],
) -> tuple[Any | None, str | None]:
    workspace_context = _agent_group_workspace_context(context)
    if workspace_context is None:
        return None, None
    manager, run_id, member_name = workspace_context
    result = await manager.acquire_workspace_write_lock(run_id, member_name)
    if not getattr(result, "ok", False):
        return None, _agent_group_error_text(result)
    return result.data, None


@builtin_tool(config=_COMPUTER_RUNTIME_TOOL_CONFIG)
@dataclass
class ExecuteShellTool(FunctionTool):
    name: str = "astrbot_execute_shell"
    description: str = "Execute a command in the shell."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute in the current runtime shell (for example, cmd.exe on Windows). Equal to 'cd {working_dir} && {your_command}'.",
                },
                "background": {
                    "type": "boolean",
                    "description": "Whether to run the command in the background.",
                    "default": False,
                },
                "env": {
                    "type": "object",
                    "description": "Optional environment variables to set for the file creation process.",
                    "additionalProperties": {"type": "string"},
                    "default": {},
                },
            },
            "required": ["command"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        command: str,
        background: bool = False,
        env: dict = {},
    ) -> ToolExecResult:
        if permission_error := check_admin_permission(context, "Shell execution"):
            return permission_error

        sb = await get_booter(
            context.context.context,
            context.context.event.unified_msg_origin,
        )
        try:
            cwd: str | None = None
            if is_local_runtime(context):
                group_cwd, group_error = await _agent_group_workspace_cwd(context)
                if group_error:
                    return f"Error executing command: {group_error}"
                if group_cwd is not None:
                    cwd = group_cwd
                else:
                    current_workspace_root = workspace_root(
                        context.context.event.unified_msg_origin
                    )
                    current_workspace_root.mkdir(parents=True, exist_ok=True)
                    cwd = str(current_workspace_root)

            write_lease, lock_error = await _acquire_agent_group_write_lease(context)
            if lock_error:
                return f"Error executing command: {lock_error}"
            try:
                result = await sb.shell.exec(
                    command,
                    cwd=cwd,
                    background=background,
                    env=env,
                )
            finally:
                if write_lease is not None:
                    await write_lease.release()
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error executing command: {str(e)}"

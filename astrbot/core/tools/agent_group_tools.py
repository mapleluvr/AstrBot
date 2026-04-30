from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import uuid
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.agent_group_runtime import AgentGroupRuntimeResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.subagent_runtime import normalize_subagent_orchestrator_config
from astrbot.core.tools.registry import builtin_tool

_RUNTIME_UNAVAILABLE = "agent_group_runtime_unavailable"
_MEMBER_CONTEXT_MISMATCH = "agent_group_member_context_mismatch"
_MEMBER_CONTEXT_REQUIRED = "agent_group_member_context_required"
_LOCAL_TOOL_FORBIDDEN_IN_MEMBER_CONTEXT = (
    "agent_group_local_tool_forbidden_in_member_context"
)


def _json_result(
    ok: bool,
    data: Any = None,
    error: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {"ok": ok, "data": data, "error": error},
        ensure_ascii=False,
    )


def _runtime_unavailable() -> str:
    return _json_result(
        False,
        error={
            "error_code": _RUNTIME_UNAVAILABLE,
            "message": "Agent group runtime manager is not available.",
            "details": None,
        },
    )


def _get_runtime_manager(context: ContextWrapper[AstrAgentContext]) -> Any:
    return getattr(context.context.context, "agent_group_runtime_manager", None)


def _get_member_context(context: ContextWrapper[AstrAgentContext]) -> dict[str, Any]:
    event = getattr(context.context, "event", None)
    if event is None or not hasattr(event, "get_extra"):
        return {}
    data = event.get_extra("agent_group_member_context")
    return data if isinstance(data, dict) else {}


def _member_context_required() -> str:
    return _json_result(
        False,
        error={
            "error_code": _MEMBER_CONTEXT_REQUIRED,
            "message": "This tool can only be called by an active agent group member.",
            "details": None,
        },
    )


def _reject_member_context_for_local_tool(
    context: ContextWrapper[AstrAgentContext],
) -> str | None:
    if not _get_member_context(context):
        return None
    return _json_result(
        False,
        error={
            "error_code": _LOCAL_TOOL_FORBIDDEN_IN_MEMBER_CONTEXT,
            "message": "Local Agent group tools cannot be called by group members.",
            "details": None,
        },
    )


def _resolve_run_id(
    context: ContextWrapper[AstrAgentContext],
    provided_run_id: str | None,
) -> tuple[str | None, str | None]:
    member_context = _get_member_context(context)
    context_run_id = member_context.get("run_id")
    if not context_run_id:
        return None, _member_context_required()
    if context_run_id and provided_run_id and context_run_id != provided_run_id:
        return None, _json_result(
            False,
            error={
                "error_code": _MEMBER_CONTEXT_MISMATCH,
                "message": "Tool run_id does not match the current agent group run.",
                "details": {
                    "context_run_id": context_run_id,
                    "provided_run_id": provided_run_id,
                },
            },
        )
    return provided_run_id or context_run_id, None


def _resolve_member_name(
    context: ContextWrapper[AstrAgentContext],
    provided_member_name: str | None,
) -> tuple[str | None, str | None]:
    member_context = _get_member_context(context)
    context_member_name = member_context.get("member_name")
    if not context_member_name:
        return None, _member_context_required()
    if (
        context_member_name
        and provided_member_name
        and context_member_name != provided_member_name
    ):
        return None, _json_result(
            False,
            error={
                "error_code": _MEMBER_CONTEXT_MISMATCH,
                "message": "Tool member name does not match the calling member.",
                "details": {
                    "context_member_name": context_member_name,
                    "provided_member_name": provided_member_name,
                },
            },
        )
    return provided_member_name or context_member_name, None


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _runtime_result_payload(result: AgentGroupRuntimeResult) -> str:
    if not result.ok:
        error = result.error
        return _json_result(
            False,
            error={
                "error_code": error.error_code if error else "agent_group_error",
                "message": error.message
                if error
                else "Agent group runtime operation failed.",
                "details": _json_safe(error.details) if error else None,
            },
        )
    return _json_result(True, data=_json_safe(result.data))


def _config_error(error_code: str, message: str, details: Any = None) -> str:
    return _json_result(
        False,
        error={
            "error_code": error_code,
            "message": message,
            "details": _json_safe(details),
        },
    )


def _get_astrbot_config(context: ContextWrapper[AstrAgentContext]) -> Any:
    return getattr(context.context.context, "astrbot_config", None)


def _draft_store(context: ContextWrapper[AstrAgentContext]) -> dict[str, dict]:
    plugin_context = context.context.context
    store = getattr(plugin_context, "_agent_preset_config_drafts", None)
    if not isinstance(store, dict):
        store = {}
        setattr(plugin_context, "_agent_preset_config_drafts", store)
    return store


def _config_hash(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_subagent_config(raw: Any) -> dict:
    return normalize_subagent_orchestrator_config(raw)


def _normalize_agent_group_config(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"summary_preset": "agent_group_summary", "presets": []}

    config = copy.deepcopy(raw)
    presets = config.get("presets")
    if not isinstance(presets, list):
        presets = []
    config["presets"] = presets
    config.setdefault("summary_preset", "agent_group_summary")

    for preset in presets:
        if not isinstance(preset, dict):
            continue
        preset.setdefault("enabled", True)
        preset.setdefault("initial_recipients", [])
        preset.setdefault("principles", [])
        preset.setdefault("collaboration_prompt", "")
        preset.setdefault("summary_preset", config["summary_preset"])
        preset.setdefault("summary_include_private", False)
        preset.setdefault("token_limit", None)
        preset.setdefault("time_limit_seconds", None)
        preset.pop("workspace_id", None)

        members = preset.get("members")
        if not isinstance(members, list):
            members = []
        normalized_members = []
        for member in members:
            if not isinstance(member, dict):
                continue
            member_name = str(member.get("name") or "").strip()
            if not member_name:
                continue
            source_type = str(member.get("source_type") or "").strip()
            subagent_preset = str(member.get("subagent_preset") or "").strip()
            persona_id = str(member.get("persona_id") or "").strip()
            if source_type not in {"subagent", "persona"}:
                source_type = (
                    "persona" if persona_id and not subagent_preset else "subagent"
                )
            if source_type == "subagent" and not subagent_preset:
                continue
            if source_type == "persona" and not persona_id:
                continue
            normalized_members.append(
                {
                    "name": member_name,
                    "source_type": source_type,
                    "subagent_preset": subagent_preset
                    if source_type == "subagent"
                    else "",
                    "persona_id": persona_id if source_type == "persona" else "",
                    "enabled": member.get("enabled", True) is not False,
                }
            )
        preset["members"] = normalized_members

    return config


def _normalize_config_section(section: str, value: Any) -> dict | None:
    if section == "agent_group":
        return _normalize_agent_group_config(value)
    if section == "subagent_orchestrator":
        return _normalize_subagent_config(value)
    return None


async def _reload_config_section(
    context: ContextWrapper[AstrAgentContext],
    section: str,
    data: dict,
) -> None:
    plugin_context = context.context.context
    if section == "agent_group":
        runtime_manager = getattr(plugin_context, "agent_group_runtime_manager", None)
        if runtime_manager is not None:
            result = runtime_manager.reload_from_config(data)
            if inspect.isawaitable(result):
                await result
        return

    orchestrator = getattr(plugin_context, "subagent_orchestrator", None)
    if orchestrator is not None:
        result = orchestrator.reload_from_config(data)
        if inspect.isawaitable(result):
            await result
    runtime_manager = getattr(plugin_context, "subagent_runtime_manager", None)
    if runtime_manager is not None:
        result = runtime_manager.reload_from_config(data)
        if inspect.isawaitable(result):
            await result


def _preset_payload(preset: Any) -> dict[str, Any]:
    return {
        "name": getattr(preset, "name", None),
        "members": [
            {
                "name": getattr(member, "name", None),
                "source_type": getattr(member, "source_type", "subagent"),
                "subagent_preset": getattr(member, "subagent_preset", None),
                "persona_id": getattr(member, "persona_id", ""),
            }
            for member in getattr(preset, "members", [])
        ],
        "initial_recipients": getattr(preset, "initial_recipients", []),
        "principles": getattr(preset, "principles", []),
        "collaboration_prompt": getattr(preset, "collaboration_prompt", ""),
        "summary_preset": getattr(preset, "summary_preset", None),
        "summary_include_private": getattr(preset, "summary_include_private", False),
        "token_limit": getattr(preset, "token_limit", None),
        "time_limit_seconds": getattr(preset, "time_limit_seconds", None),
    }


@builtin_tool
@dataclass
class ListAgentGroupPresetsTool(FunctionTool[AstrAgentContext]):
    name: str = "list_agent_group_presets"
    description: str = "List available agent group presets."
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        del kwargs
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        return _json_result(
            True,
            data={
                "presets": [
                    _preset_payload(preset) for preset in manager.list_presets()
                ]
            },
        )


@builtin_tool
@dataclass
class StartAgentGroupTool(FunctionTool[AstrAgentContext]):
    name: str = "start_agent_group"
    description: str = "Start an agent group run from a preset."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "preset_name": {"type": "string", "description": "Agent group preset."},
                "task": {"type": "string", "description": "Task for the group."},
                "workspace_id": {
                    "type": "string",
                    "description": "Optional workspace ID.",
                },
            },
            "required": ["preset_name", "task"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        result = await manager.start_run(
            context.context.event,
            kwargs.get("preset_name"),
            kwargs.get("task"),
            workspace_id=kwargs.get("workspace_id"),
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class GetAgentGroupStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "get_agent_group_status"
    description: str = "Get an agent group run status."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "workspace_id": {"type": "string"},
                "include_private": {
                    "type": "boolean",
                    "description": "Include private member messages. Defaults to false.",
                },
            },
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        result = await manager.get_status(
            run_id=kwargs.get("run_id"),
            workspace_id=kwargs.get("workspace_id"),
            include_private=bool(kwargs.get("include_private", False)),
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class ListAgentGroupRunsTool(FunctionTool[AstrAgentContext]):
    name: str = "list_agent_group_runs"
    description: str = "List agent group runs, optionally filtered by workspace/status."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "umo": {"type": "string"},
                "workspace_id": {"type": "string"},
                "status": {"type": "string"},
                "include_private": {
                    "type": "boolean",
                    "description": "Include private member messages. Defaults to false.",
                },
            },
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        result = await manager.list_runs(
            umo=kwargs.get("umo"),
            workspace_id=kwargs.get("workspace_id"),
            status=kwargs.get("status"),
            include_private=bool(kwargs.get("include_private", False)),
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class SendAgentGroupInputTool(FunctionTool[AstrAgentContext]):
    name: str = "send_agent_group_input"
    description: str = "Send additional input into an active agent group run."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["run_id", "content"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        result = await manager.send_input(
            kwargs.get("run_id"),
            kwargs.get("content"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class CancelAgentGroupTool(FunctionTool[AstrAgentContext]):
    name: str = "cancel_agent_group"
    description: str = "Cancel an active agent group run."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["run_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        result = await manager.cancel_run(
            kwargs.get("run_id"),
            kwargs.get("reason"),
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class DraftAgentPresetConfigPatchTool(FunctionTool[AstrAgentContext]):
    name: str = "draft_agent_preset_config_patch"
    description: str = (
        "Draft a SubAgent or Agent Group preset config change for user confirmation."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["agent_group", "subagent_orchestrator"],
                    "description": "Config section to modify.",
                },
                "config": {
                    "type": "object",
                    "description": "Full proposed replacement config section.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason shown with the draft.",
                },
            },
            "required": ["section", "config"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        cfg = _get_astrbot_config(context)
        if cfg is None:
            return _config_error(
                "config_unavailable",
                "AstrBot config is not available.",
            )
        section = str(kwargs.get("section") or "").strip()
        proposed_config = kwargs.get("config")
        if not isinstance(proposed_config, dict):
            return _config_error(
                "invalid_config",
                "Proposed config must be a JSON object.",
            )
        before = _normalize_config_section(section, cfg.get(section))
        after = _normalize_config_section(section, proposed_config)
        if before is None or after is None:
            return _config_error(
                "invalid_section",
                "Config section must be agent_group or subagent_orchestrator.",
                {"section": section},
            )
        current_hash = _config_hash(before)
        draft_id = uuid.uuid4().hex
        draft = {
            "draft_id": draft_id,
            "section": section,
            "current_hash": current_hash,
            "proposed_hash": _config_hash(after),
            "before": before,
            "after": after,
            "reason": str(kwargs.get("reason") or ""),
        }
        _draft_store(context)[draft_id] = copy.deepcopy(draft)
        return _json_result(
            True,
            data={
                **draft,
                "requires_confirmation": True,
            },
        )


@builtin_tool
@dataclass
class ApplyAgentPresetConfigPatchTool(FunctionTool[AstrAgentContext]):
    name: str = "apply_agent_preset_config_patch"
    description: str = "Apply a confirmed preset config draft patch."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "description": "Draft ID returned by draft_agent_preset_config_patch.",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Must be true after explicit user confirmation.",
                },
            },
            "required": ["draft_id", "confirmed"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        forbidden_payload = _reject_member_context_for_local_tool(context)
        if forbidden_payload:
            return forbidden_payload
        if kwargs.get("confirmed") is not True:
            return _config_error(
                "confirmation_required",
                "User confirmation is required before applying this draft.",
            )
        cfg = _get_astrbot_config(context)
        if cfg is None:
            return _config_error(
                "config_unavailable",
                "AstrBot config is not available.",
            )
        draft_id = str(kwargs.get("draft_id") or "").strip()
        store = _draft_store(context)
        draft = store.get(draft_id)
        if draft is None:
            return _config_error(
                "draft_not_found",
                "Preset config draft was not found.",
                {"draft_id": draft_id},
            )

        section = draft["section"]
        current = _normalize_config_section(section, cfg.get(section))
        if current is None:
            return _config_error(
                "invalid_section",
                "Config section must be agent_group or subagent_orchestrator.",
                {"section": section},
            )
        current_hash = _config_hash(current)
        if current_hash != draft["current_hash"]:
            return _config_error(
                "stale_config",
                "Config changed after the draft was created.",
                {
                    "draft_id": draft_id,
                    "section": section,
                    "expected_hash": draft["current_hash"],
                    "actual_hash": current_hash,
                },
            )

        cfg[section] = copy.deepcopy(draft["after"])
        cfg.save_config()
        await _reload_config_section(context, section, cfg[section])
        store.pop(draft_id, None)
        return _json_result(
            True,
            data={
                "applied": True,
                "draft_id": draft_id,
                "section": section,
                "config": _json_safe(cfg[section]),
            },
        )


@builtin_tool
@dataclass
class MsgToAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "msg_to_agent"
    description: str = "Send a private message to another member in the agent group."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "from_member": {"type": "string"},
                "to_member": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["to_member", "content"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        from_member, error_payload = _resolve_member_name(
            context,
            kwargs.get("from_member"),
        )
        if error_payload:
            return error_payload
        result = await manager.msg_to_agent(
            run_id,
            from_member=from_member,
            to_member=kwargs.get("to_member"),
            content=kwargs.get("content"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class MsgToGroupTool(FunctionTool[AstrAgentContext]):
    name: str = "msg_to_group"
    description: str = "Send a message to the whole agent group."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "from_member": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["content"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        from_member, error_payload = _resolve_member_name(
            context,
            kwargs.get("from_member"),
        )
        if error_payload:
            return error_payload
        result = await manager.msg_to_group(
            run_id,
            from_member=from_member,
            content=kwargs.get("content"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class MarkCompleteTool(FunctionTool[AstrAgentContext]):
    name: str = "mark_complete"
    description: str = "Mark this member complete with a final opinion."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "member_name": {"type": "string"},
                "final_opinion": {"type": "string"},
            },
            "required": ["final_opinion"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        member_name, error_payload = _resolve_member_name(
            context,
            kwargs.get("member_name"),
        )
        if error_payload:
            return error_payload
        result = await manager.mark_complete(
            run_id,
            member_name,
            kwargs.get("final_opinion"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class RevokeCompleteTool(FunctionTool[AstrAgentContext]):
    name: str = "revoke_complete"
    description: str = "Reopen a completed member in an agent group run."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "member_name": {"type": "string"},
            },
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        member_name, error_payload = _resolve_member_name(
            context,
            kwargs.get("member_name"),
        )
        if error_payload:
            return error_payload
        result = await manager.revoke_complete(
            run_id,
            member_name,
            actor_member=_get_member_context(context).get("member_name"),
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class GetGroupStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "get_group_status"
    description: str = "Get the current agent group status."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        result = await manager.get_status(run_id=run_id)
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class AskLocalAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "ask_local_agent"
    description: str = "Ask the local agent for input during an agent group run."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "from_member": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["question"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        from_member, error_payload = _resolve_member_name(
            context,
            kwargs.get("from_member"),
        )
        if error_payload:
            return error_payload
        result = await manager.ask_local_agent(
            run_id,
            from_member=from_member,
            question=kwargs.get("question"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class CreateGroupSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "create_group_subagent"
    description: str = "Create a temporary helper SubAgent for this group member."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "from_member": {"type": "string"},
                "helper_name": {"type": "string"},
                "preset_name": {"type": "string"},
            },
            "required": ["helper_name", "preset_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        from_member, error_payload = _resolve_member_name(
            context,
            kwargs.get("from_member"),
        )
        if error_payload:
            return error_payload
        result = await manager.create_helper_subagent(
            run_id,
            from_member=from_member,
            helper_name=kwargs.get("helper_name"),
            preset_name=kwargs.get("preset_name"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class RunGroupSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "run_group_subagent"
    description: str = "Run one of this member's temporary helper SubAgents."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "from_member": {"type": "string"},
                "helper_name": {"type": "string"},
                "input_text": {"type": "string"},
            },
            "required": ["helper_name", "input_text"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        from_member, error_payload = _resolve_member_name(
            context,
            kwargs.get("from_member"),
        )
        if error_payload:
            return error_payload
        result = await manager.run_helper_subagent(
            run_id,
            from_member=from_member,
            helper_name=kwargs.get("helper_name"),
            input_text=kwargs.get("input_text"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class ResetGroupSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "reset_group_subagent"
    description: str = "Reset one of this member's temporary helper SubAgents."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "from_member": {"type": "string"},
                "helper_name": {"type": "string"},
            },
            "required": ["helper_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        from_member, error_payload = _resolve_member_name(
            context,
            kwargs.get("from_member"),
        )
        if error_payload:
            return error_payload
        result = await manager.reset_helper_subagent(
            run_id,
            from_member=from_member,
            helper_name=kwargs.get("helper_name"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
        )
        return _runtime_result_payload(result)


@builtin_tool
@dataclass
class DeleteGroupSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "delete_group_subagent"
    description: str = "Delete one of this member's temporary helper SubAgents."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "from_member": {"type": "string"},
                "helper_name": {"type": "string"},
            },
            "required": ["helper_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        run_id, error_payload = _resolve_run_id(context, kwargs.get("run_id"))
        if error_payload:
            return error_payload
        from_member, error_payload = _resolve_member_name(
            context,
            kwargs.get("from_member"),
        )
        if error_payload:
            return error_payload
        result = await manager.delete_helper_subagent(
            run_id,
            from_member=from_member,
            helper_name=kwargs.get("helper_name"),
            actor_member=_get_member_context(context).get("member_name"),
            event=context.context.event,
            runtime_context=context.context.context,
        )
        return _runtime_result_payload(result)


AGENT_GROUP_LOCAL_AGENT_TOOLS = (
    ListAgentGroupPresetsTool,
    ListAgentGroupRunsTool,
    StartAgentGroupTool,
    GetAgentGroupStatusTool,
    SendAgentGroupInputTool,
    CancelAgentGroupTool,
    DraftAgentPresetConfigPatchTool,
    ApplyAgentPresetConfigPatchTool,
)

AGENT_GROUP_MEMBER_TOOLS = (
    MsgToAgentTool,
    MsgToGroupTool,
    MarkCompleteTool,
    RevokeCompleteTool,
    GetGroupStatusTool,
    AskLocalAgentTool,
    CreateGroupSubAgentTool,
    RunGroupSubAgentTool,
    ResetGroupSubAgentTool,
    DeleteGroupSubAgentTool,
)

AGENT_GROUP_MANAGEMENT_TOOLS = AGENT_GROUP_LOCAL_AGENT_TOOLS + AGENT_GROUP_MEMBER_TOOLS


__all__ = [
    "AGENT_GROUP_LOCAL_AGENT_TOOLS",
    "AGENT_GROUP_MANAGEMENT_TOOLS",
    "AGENT_GROUP_MEMBER_TOOLS",
    "ApplyAgentPresetConfigPatchTool",
    "AskLocalAgentTool",
    "CancelAgentGroupTool",
    "CreateGroupSubAgentTool",
    "DeleteGroupSubAgentTool",
    "DraftAgentPresetConfigPatchTool",
    "GetAgentGroupStatusTool",
    "GetGroupStatusTool",
    "ListAgentGroupRunsTool",
    "ListAgentGroupPresetsTool",
    "MarkCompleteTool",
    "MsgToAgentTool",
    "MsgToGroupTool",
    "ResetGroupSubAgentTool",
    "RevokeCompleteTool",
    "RunGroupSubAgentTool",
    "SendAgentGroupInputTool",
    "StartAgentGroupTool",
]

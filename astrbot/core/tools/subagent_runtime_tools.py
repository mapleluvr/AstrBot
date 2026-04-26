from __future__ import annotations

import dataclasses
import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.subagent_runtime import SubAgentRuntimeResult
from astrbot.core.tools.registry import builtin_tool

_RUNTIME_UNAVAILABLE = "subagent_runtime_unavailable"


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
            "message": "Sub-agent runtime manager is not available.",
            "details": None,
        },
    )


def _get_runtime_manager(context: ContextWrapper[AstrAgentContext]) -> Any:
    return getattr(context.context.context, "subagent_runtime_manager", None)


def _value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _compact_dict(obj: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: _json_safe(_value(obj, key))
        for key in keys
        if _value(obj, key) is not None
    }


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _preset_payload(preset: Any) -> dict[str, Any]:
    return _compact_dict(
        preset,
        (
            "name",
            "runtime_mode",
            "public_description",
            "provider_id",
            "persona_id",
            "tools",
            "skills",
        ),
    )


def _instance_payload(instance: Any) -> dict[str, Any]:
    return _compact_dict(
        instance,
        (
            "instance_id",
            "name",
            "preset_name",
            "scope_type",
            "scope_id",
            "provider_id",
            "persona_id",
            "tools",
            "skills",
            "version",
        ),
    )


def _runtime_result_payload(
    result: SubAgentRuntimeResult,
    *,
    action_key: str,
) -> str:
    if not result.ok:
        error = result.error
        return _json_result(
            False,
            error={
                "error_code": error.error_code if error else "subagent_runtime_error",
                "message": error.message
                if error
                else "Sub-agent runtime operation failed.",
                "details": _json_safe(error.details) if error else None,
            },
        )

    return _json_result(
        True,
        data={action_key: True, "instance": _instance_payload(result.data)},
    )


def _runtime_run_payload(result: SubAgentRuntimeResult) -> str:
    if not result.ok:
        error = result.error
        return _json_result(
            False,
            error={
                "error_code": error.error_code if error else "subagent_runtime_error",
                "message": error.message
                if error
                else "Sub-agent runtime operation failed.",
                "details": _json_safe(error.details) if error else None,
            },
        )
    return _json_result(True, data=_json_safe(result.data))


@builtin_tool
@dataclass
class ListSubAgentPresetsTool(FunctionTool[AstrAgentContext]):
    name: str = "list_subagent_presets"
    description: str = (
        "List persistent sub-agent presets available for creating runtime instances."
    )
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        del kwargs
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        presets = manager.list_presets(runtime_mode="persistent")
        return _json_result(
            True,
            data={"presets": [_preset_payload(preset) for preset in presets]},
        )


@builtin_tool
@dataclass
class CreateSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "create_subagent"
    description: str = "Create a persistent sub-agent instance from a preset."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the new instance."},
                "preset_name": {
                    "type": "string",
                    "description": "Persistent preset name.",
                },
                "scope_type": {
                    "type": "string",
                    "enum": ["conversation", "session"],
                    "description": "Optional scope for the instance.",
                },
                "overrides": {
                    "type": "object",
                    "description": "Optional provider, persona, prompt, tool, or skill overrides.",
                },
            },
            "required": ["name", "preset_name"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        result = await manager.create_instance(
            context.context.event,
            kwargs.get("name"),
            kwargs.get("preset_name"),
            scope_type=kwargs.get("scope_type"),
            overrides=kwargs.get("overrides") or {},
        )
        return _runtime_result_payload(result, action_key="created")


@builtin_tool
@dataclass
class ListSubAgentsTool(FunctionTool[AstrAgentContext]):
    name: str = "list_subagents"
    description: str = (
        "List persistent sub-agent instances available in the current context."
    )
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        del kwargs
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        instances = await manager.list_instances(context.context.event)
        return _json_result(
            True,
            data={
                "instances": [_instance_payload(instance) for instance in instances],
            },
        )


@builtin_tool
@dataclass
class RunSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "run_subagent"
    description: str = "Run a persistent sub-agent instance."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Sub-agent instance name."},
                "input": {
                    "type": "string",
                    "description": "Message or task for the sub-agent.",
                },
                "scope_type": {
                    "type": "string",
                    "enum": ["conversation", "session"],
                    "description": "Optional instance scope.",
                },
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional image URLs to include with the sub-agent input.",
                },
            },
            "required": ["name", "input"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        event = context.context.event
        had_runtime_context = False
        previous_runtime_context = None
        if hasattr(event, "get_extra") and hasattr(event, "set_extra"):
            previous_runtime_context = event.get_extra("subagent_runtime_context")
            had_runtime_context = previous_runtime_context is not None
            event.set_extra("subagent_runtime_context", context.context.context)
        try:
            result = await manager.run_instance(
                event,
                kwargs.get("name"),
                kwargs.get("input"),
                image_urls=kwargs.get("image_urls"),
                scope_type=kwargs.get("scope_type"),
                background_task=kwargs.get("background_task", False),
            )
        finally:
            if hasattr(event, "set_extra"):
                if had_runtime_context:
                    event.set_extra(
                        "subagent_runtime_context", previous_runtime_context
                    )
                elif hasattr(event, "_extras"):
                    event._extras.pop("subagent_runtime_context", None)
        return _runtime_run_payload(result)


@builtin_tool
@dataclass
class UpdateSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "update_subagent"
    description: str = "Update a persistent sub-agent instance's runtime configuration."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Sub-agent instance name."},
                "scope_type": {
                    "type": "string",
                    "enum": ["conversation", "session"],
                    "description": "Optional instance scope.",
                },
                "updates": {"type": "object", "description": "Fields to update."},
            },
            "required": ["name", "updates"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        result = await manager.update_instance(
            context.context.event,
            kwargs.get("name"),
            scope_type=kwargs.get("scope_type"),
            updates=kwargs.get("updates") or {},
        )
        return _runtime_result_payload(result, action_key="updated")


@builtin_tool
@dataclass
class ResetSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "reset_subagent"
    description: str = (
        "Reset a persistent sub-agent instance's persisted conversation state."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Sub-agent instance name."},
                "scope_type": {
                    "type": "string",
                    "enum": ["conversation", "session"],
                    "description": "Optional instance scope.",
                },
            },
            "required": ["name"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        result = await manager.reset_instance(
            context.context.event,
            kwargs.get("name"),
            scope_type=kwargs.get("scope_type"),
        )
        return _runtime_result_payload(result, action_key="reset")


@builtin_tool
@dataclass
class DeleteSubAgentTool(FunctionTool[AstrAgentContext]):
    name: str = "delete_subagent"
    description: str = "Delete a persistent sub-agent instance."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Sub-agent instance name."},
                "scope_type": {
                    "type": "string",
                    "enum": ["conversation", "session"],
                    "description": "Optional instance scope.",
                },
            },
            "required": ["name"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        manager = _get_runtime_manager(context)
        if manager is None:
            return _runtime_unavailable()
        result = await manager.delete_instance(
            context.context.event,
            kwargs.get("name"),
            scope_type=kwargs.get("scope_type"),
        )
        return _runtime_result_payload(result, action_key="deleted")


SUBAGENT_RUNTIME_MANAGEMENT_TOOLS = (
    ListSubAgentPresetsTool,
    CreateSubAgentTool,
    ListSubAgentsTool,
    RunSubAgentTool,
    UpdateSubAgentTool,
    ResetSubAgentTool,
    DeleteSubAgentTool,
)


__all__ = [
    "CreateSubAgentTool",
    "DeleteSubAgentTool",
    "ListSubAgentPresetsTool",
    "ListSubAgentsTool",
    "ResetSubAgentTool",
    "RunSubAgentTool",
    "SUBAGENT_RUNTIME_MANAGEMENT_TOOLS",
    "UpdateSubAgentTool",
]

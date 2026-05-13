from __future__ import annotations

import asyncio
import copy
import inspect
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy.exc import IntegrityError

from astrbot import logger
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import Message
from astrbot.core.agent.run_context import ContextWrapper

PRESET_NOT_FOUND = "preset_not_found"
INSTANCE_NOT_FOUND = "instance_not_found"
INSTANCE_EXISTS = "instance_exists"
INSTANCE_BUSY = "instance_busy"
VERSION_CONFLICT = "version_conflict"
SUBAGENT_EXECUTION_FAILED = "subagent_execution_failed"
MAX_INSTANCES_REACHED = "max_instances_reached"
INVALID_TOOL = "invalid_tool"
INVALID_SKILL = "invalid_skill"
AMBIGUOUS_INSTANCE = "ambiguous_instance"
INVALID_UPDATE_FIELD = "invalid_update_field"
RUNTIME_DISABLED = "runtime_disabled"
AGENT_GROUP_SUMMARY_PRESET_NAME = "agent_group_summary"


def default_agent_group_summary_preset() -> dict[str, Any]:
    return {
        "name": AGENT_GROUP_SUMMARY_PRESET_NAME,
        "enabled": True,
        "runtime_mode": "persistent",
        "persona_id": None,
        "provider_id": None,
        "public_description": "Summarizes completed Agent Group runs.",
        "system_prompt": (
            "You summarize completed Agent Group runs for the Local Agent. "
            "Use only the provided transcript and final opinions. "
            "Return a concise final answer with decisions, disagreements, "
            "risks, and next steps."
        ),
        "tools": [],
        "skills": [],
    }


def normalize_subagent_orchestrator_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        config = copy.deepcopy(raw)
    else:
        config = {
            "main_enable": False,
            "remove_main_duplicate_tools": False,
            "agents": [],
        }

    if "main_enable" not in config and "enable" in config:
        config["main_enable"] = bool(config.get("enable", False))
    config.setdefault("main_enable", False)
    config.setdefault("remove_main_duplicate_tools", False)

    agents = config.get("agents")
    if not isinstance(agents, list):
        agents = []
    normalized_agents = []
    has_summary = False
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        normalized = copy.deepcopy(agent)
        if normalized.get("name") == AGENT_GROUP_SUMMARY_PRESET_NAME:
            has_summary = True
            normalized_agents.append(normalized)
            continue
        normalized.setdefault("provider_id", None)
        normalized.setdefault("persona_id", None)
        normalized.setdefault("checkpoint_async_enabled", None)
        normalized.setdefault("checkpoint_async_provider_id", None)
        normalized_agents.append(normalized)

    if not has_summary:
        normalized_agents.append(default_agent_group_summary_preset())

    config["agents"] = normalized_agents
    return config


_MUTABLE_INSTANCE_FIELDS = {
    "provider_id",
    "persona_id",
    "system_prompt",
    "system_prompt_delta",
    "tools",
    "skills",
    "max_persisted_turns",
    "max_persisted_tokens",
    "checkpoint_async_enabled",
    "checkpoint_async_provider_id",
}


@dataclass
class SubAgentPreset:
    name: str
    runtime_mode: str = "handoff"
    instructions: str | None = None
    public_description: str | None = None
    provider_id: str | None = None
    persona_id: str | None = None
    tools: list | None = None
    skills: list | None = None
    begin_dialogs: list | None = None
    checkpoint_async_enabled: bool | None = None
    """null = inherit category default, true = enabled, false = disabled"""
    checkpoint_async_provider_id: str | None = None
    """null = inherit from global/provider chain"""


@dataclass
class SubAgentRuntimeError:
    error_code: str
    message: str
    details: Any = None


@dataclass
class SubAgentRuntimeResult:
    ok: bool
    data: Any = None
    error: SubAgentRuntimeError | None = None

    @classmethod
    def success(cls, data: Any = None) -> SubAgentRuntimeResult:
        return cls(ok=True, data=data)

    @classmethod
    def failure(
        cls,
        error_code: str,
        message: str,
        details: Any = None,
    ) -> SubAgentRuntimeResult:
        return cls(
            ok=False,
            error=SubAgentRuntimeError(error_code, message, details),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": asdict(self.error) if self.error else None,
        }


class _InstanceLockResult(SubAgentRuntimeResult):
    def __init__(self, manager: SubAgentRuntimeManager, instance_id: str, ok: bool):
        super().__init__(ok=ok)
        self._manager = manager
        self._instance_id = instance_id
        if not ok:
            self.error = SubAgentRuntimeError(
                INSTANCE_BUSY,
                "Sub-agent instance is busy.",
            )

    async def __aenter__(self) -> _InstanceLockResult:
        if not self.ok:
            raise RuntimeError(self.error.message if self.error else "Lock unavailable")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._manager._instance_locks.pop(self._instance_id, None)


class _BackgroundRunHooks(BaseAgentRunHooks[Any]):
    def __init__(self, manager: SubAgentRuntimeManager, task_id: str):
        self._manager = manager
        self._task_id = task_id

    async def on_agent_begin(self, run_context: ContextWrapper[Any]) -> None:
        return None

    async def on_tool_start(
        self,
        run_context: ContextWrapper[Any],
        tool,
        tool_args: dict | None,
    ) -> None:
        tool_name = getattr(tool, "name", None)
        await self._manager._safe_append_background_run_event(
            self._task_id,
            "tool_call",
            f"Called tool {tool_name}.",
            tool_name=tool_name,
        )

    async def on_tool_end(
        self,
        run_context: ContextWrapper[Any],
        tool,
        tool_args: dict | None,
        tool_result,
    ) -> None:
        if tool_result is None or not getattr(tool_result, "isError", False):
            return
        tool_name = getattr(tool, "name", None)
        error_text = self._tool_result_text(tool_result)
        if error_text:
            message = f"Tool {tool_name} failed: {error_text}"
        else:
            message = f"Tool {tool_name} failed."
        await self._manager._safe_append_background_run_event(
            self._task_id,
            "tool_error",
            message,
            tool_name=tool_name,
        )

    async def on_agent_done(
        self, run_context: ContextWrapper[Any], llm_response
    ) -> None:
        return None

    @staticmethod
    def _tool_result_text(tool_result) -> str:
        parts: list[str] = []
        for item in getattr(tool_result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
                continue
            resource = getattr(item, "resource", None)
            resource_text = getattr(resource, "text", None)
            if resource_text:
                parts.append(str(resource_text))
        return "\n\n".join(part for part in parts if part).strip()


class SubAgentRuntimeManager:
    def __init__(
        self,
        db,
        tool_mgr,
        persona_mgr,
        conversation_manager,
        config=None,
        skill_manager=None,
    ):
        self.db = db
        self.tool_mgr = tool_mgr
        self.persona_mgr = persona_mgr
        self.conversation_manager = conversation_manager
        self.skill_manager = skill_manager
        self.presets: dict[str, SubAgentPreset] = {}
        self._instance_locks: dict[str, bool] = {}
        self._instance_lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._background_run_terminal_overrides: dict[str, dict[str, Any]] = {}
        self._scope_create_locks: dict[str, asyncio.Lock] = {}
        self.runtime_enabled = False
        self.max_instances_per_scope = 8
        self.max_persisted_turns = 20
        self.max_persisted_tokens = None
        self.max_background_run_events = 10
        if config is not None:
            self.reload_from_config(config)

    def reload_from_config(self, cfg):
        cfg = normalize_subagent_orchestrator_config(cfg)
        runtime_cfg = cfg.get("runtime", {}) if isinstance(cfg, dict) else {}
        if not isinstance(runtime_cfg, dict):
            runtime_cfg = {}

        self.runtime_enabled = bool(
            runtime_cfg.get("enable", cfg.get("runtime_enable", False))
        )
        self.max_instances_per_scope = runtime_cfg.get(
            "max_instances_per_scope",
            cfg.get("max_instances_per_scope", 8),
        )
        self.max_persisted_turns = runtime_cfg.get(
            "max_persisted_turns",
            cfg.get("max_persisted_turns", 20),
        )
        self.max_persisted_tokens = runtime_cfg.get(
            "max_persisted_tokens",
            cfg.get("max_persisted_tokens"),
        )
        self.presets = {preset.name: preset for preset in self.normalize_presets(cfg)}

    def normalize_presets(self, cfg) -> list[SubAgentPreset]:
        cfg = normalize_subagent_orchestrator_config(cfg)
        presets = []
        for agent in cfg.get("agents", []):
            if not isinstance(agent, dict):
                continue
            if agent.get("enabled", agent.get("enable", True)) is False:
                continue
            name = agent.get("name")
            if not name:
                continue

            persona_id = agent.get("persona_id")
            persona = None
            if persona_id:
                persona = self.persona_mgr.get_persona_v3_by_id(persona_id)

            presets.append(
                SubAgentPreset(
                    name=name,
                    runtime_mode=agent.get("runtime_mode") or "handoff",
                    instructions=self._persona_value(persona, "prompt")
                    if persona is not None
                    else agent.get("system_prompt")
                    or agent.get("instructions")
                    or agent.get("prompt"),
                    public_description=agent.get("public_description"),
                    provider_id=agent.get("provider_id"),
                    persona_id=persona_id,
                    tools=self._capability_value(agent, persona, "tools"),
                    skills=self._capability_value(agent, persona, "skills"),
                    begin_dialogs=list(
                        self._persona_value(persona, "_begin_dialogs_processed")
                        or self._persona_value(persona, "begin_dialogs")
                        or agent.get("begin_dialogs")
                        or []
                    ),
                )
            )
        return presets

    async def resolve_scope(self, event, scope_type=None):
        umo = event.unified_msg_origin
        scope_type = scope_type or "conversation"
        if scope_type == "session":
            return umo, "session", umo

        conversation_id = await self._maybe_await(
            self.conversation_manager.get_curr_conversation_id(umo)
        )
        if not conversation_id:
            conversation_id = await self._maybe_await(
                self.conversation_manager.new_conversation(
                    umo,
                    event.get_platform_id(),
                )
            )
        return umo, "conversation", conversation_id

    def list_presets(self, runtime_mode=None) -> list[SubAgentPreset]:
        presets = list(self.presets.values())
        if runtime_mode is not None:
            return [preset for preset in presets if preset.runtime_mode == runtime_mode]
        return presets

    async def cleanup_for_session(self, umo: str) -> None:
        instances = await self.db.list_subagent_instances(
            umo=umo,
            scope_type="session",
            scope_id=umo,
        )
        await self._cleanup_instances(instances)

    async def cleanup_for_conversation(self, conversation_id: str) -> None:
        conversation = await self.db.get_conversation_by_id(conversation_id)
        if conversation is None:
            return

        instances = await self.db.list_subagent_instances(
            umo=conversation.user_id,
            scope_type="conversation",
            scope_id=conversation_id,
        )
        await self._cleanup_instances(instances)

    async def _cleanup_instances(self, instances) -> None:
        for instance in instances:
            deleted = await self.delete_instance_by_id(instance.instance_id)
            if deleted.ok:
                continue
            if deleted.error is not None and deleted.error.error_code == INSTANCE_BUSY:
                continue
            logger.warning(
                "Failed to delete sub-agent instance "
                f"'{instance.instance_id}' during cleanup."
            )

    async def list_instances(self, event):
        if not self.runtime_enabled:
            return self._runtime_disabled_result()
        umo, _, conversation_id = await self.resolve_scope(event, "conversation")
        conversation_instances = await self.db.list_subagent_instances(
            umo=umo,
            scope_type="conversation",
            scope_id=conversation_id,
        )
        session_instances = await self.db.list_subagent_instances(
            umo=umo,
            scope_type="session",
            scope_id=umo,
        )
        return conversation_instances + session_instances

    @staticmethod
    def _background_event(event_type: str, message: str, **details) -> dict[str, Any]:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "message": message,
        }
        payload.update(
            {key: value for key, value in details.items() if value is not None}
        )
        return payload

    async def _append_background_run_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        **details,
    ) -> None:
        await self.db.append_subagent_background_run_event(
            task_id,
            self._background_event(event_type, message, **details),
            max_events=self.max_background_run_events,
        )

    async def _safe_append_background_run_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        **details,
    ) -> None:
        try:
            await self._append_background_run_event(
                task_id,
                event_type,
                message,
                **details,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Failed to append background run event "
                f"'{event_type}' for task '{task_id}':\n"
                f"{traceback.format_exc()}"
            )

    async def _best_effort_update_background_run(
        self,
        task_id: str,
        action: str,
        **updates,
    ):
        try:
            return await self.db.update_subagent_background_run(task_id, **updates)
        except asyncio.CancelledError:
            logger.warning(
                "Cancelled while "
                f"{action} for background run '{task_id}':\n"
                f"{traceback.format_exc()}"
            )
        except Exception:
            logger.warning(
                "Failed while "
                f"{action} for background run '{task_id}':\n"
                f"{traceback.format_exc()}"
            )
        return None

    async def _best_effort_append_background_run_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        **details,
    ) -> None:
        try:
            await self._append_background_run_event(
                task_id,
                event_type,
                message,
                **details,
            )
        except asyncio.CancelledError:
            logger.warning(
                "Cancelled while appending background run event "
                f"'{event_type}' for task '{task_id}':\n"
                f"{traceback.format_exc()}"
            )
        except Exception:
            logger.warning(
                "Failed to append background run event "
                f"'{event_type}' for task '{task_id}':\n"
                f"{traceback.format_exc()}"
            )

    async def _best_effort_mark_background_run_failed(
        self,
        task_id: str,
        error_message: str,
    ) -> None:
        await self._best_effort_update_background_run(
            task_id,
            "persisting failed status",
            status="failed",
            error_message=error_message,
            completed_at=datetime.now(timezone.utc),
        )
        await self._best_effort_append_background_run_event(
            task_id,
            "failed",
            f"Background run failed: {error_message}",
        )

    def _discard_background_task(
        self,
        task_id: str,
        task: asyncio.Task | None = None,
    ) -> None:
        if task is not None and self._background_tasks.get(task_id) is not task:
            return
        self._background_tasks.pop(task_id, None)

    def _get_live_background_task(self, task_id: str) -> asyncio.Task | None:
        task = self._background_tasks.get(task_id)
        if task is None:
            return None
        if task.done():
            self._discard_background_task(task_id, task)
            return None
        return task

    def _track_background_task(self, task_id: str, task: asyncio.Task) -> None:
        self._background_tasks[task_id] = task
        task.add_done_callback(
            lambda finished_task, task_id=task_id: self._discard_background_task(
                task_id, finished_task
            )
        )

    @staticmethod
    def _background_run_with_overrides(run, **overrides):
        if run is None:
            return None
        payload = {
            "task_id": getattr(run, "task_id", None),
            "status": getattr(run, "status", None),
            "created_at": getattr(run, "created_at", None),
            "updated_at": getattr(run, "updated_at", None),
            "completed_at": getattr(run, "completed_at", None),
            "final_response": getattr(run, "final_response", None),
            "error_message": getattr(run, "error_message", None),
            "token_usage": getattr(run, "token_usage", None),
            "events": list(getattr(run, "events", []) or []),
        }
        payload.update(overrides)
        return SimpleNamespace(**payload)

    @staticmethod
    def _background_run_is_terminal(run) -> bool:
        return run is not None and getattr(run, "status", None) not in {
            "queued",
            "running",
        }

    @staticmethod
    def _background_run_has_durable_completion_markers(run) -> bool:
        return bool(
            getattr(run, "completed_at", None) is not None
            or getattr(run, "final_response", None) is not None
        )

    async def _reconcile_background_run(self, run):
        if run is None:
            return run
        if self._background_run_is_terminal(run):
            self._background_run_terminal_overrides.pop(run.task_id, None)
            return run
        if self._get_live_background_task(run.task_id) is not None:
            return run
        terminal_override = self._background_run_terminal_overrides.get(run.task_id)
        if terminal_override is not None:
            if terminal_override.get("status") == "completed":
                updated_run = await self._best_effort_update_background_run(
                    run.task_id,
                    "persisting completed status during reconcile",
                    status="completed",
                    final_response=terminal_override.get("final_response"),
                    error_message=None,
                    token_usage=terminal_override.get("token_usage"),
                    completed_at=terminal_override.get("completed_at"),
                )
                refreshed_run = await self.db.get_subagent_background_run(run.task_id)
                terminal_run = None
                if self._background_run_is_terminal(refreshed_run):
                    terminal_run = refreshed_run
                elif self._background_run_is_terminal(updated_run):
                    terminal_run = updated_run
                if terminal_run is not None:
                    self._background_run_terminal_overrides.pop(run.task_id, None)
                    return terminal_run
            return self._background_run_with_overrides(run, **terminal_override)
        if self._background_run_has_durable_completion_markers(run):
            completed_override = {
                "status": "completed",
                "final_response": getattr(run, "final_response", None),
                "error_message": None,
                "token_usage": getattr(run, "token_usage", None),
                "completed_at": getattr(run, "completed_at", None),
            }
            try:
                updated_run = await self.db.update_subagent_background_run(
                    run.task_id,
                    status="completed",
                    final_response=completed_override["final_response"],
                    error_message=None,
                    token_usage=completed_override["token_usage"],
                    completed_at=completed_override["completed_at"],
                )
            except Exception:
                return self._background_run_with_overrides(run, **completed_override)
            refreshed_run = await self.db.get_subagent_background_run(run.task_id)
            return refreshed_run or updated_run or self._background_run_with_overrides(
                run, **completed_override
            )

        error_message = "Background run state was stale or interrupted; no active runtime task was found."
        updated_run = await self.db.update_subagent_background_run(
            run.task_id,
            status="failed",
            error_message=error_message,
            completed_at=datetime.now(timezone.utc),
        )
        await self._safe_append_background_run_event(
            run.task_id,
            "failed",
            f"Background run failed: {error_message}",
        )
        refreshed_run = await self.db.get_subagent_background_run(run.task_id)
        return refreshed_run or updated_run or run

    @staticmethod
    def _background_run_payload(run) -> dict[str, Any] | None:
        if run is None:
            return None
        return {
            "task_id": run.task_id,
            "status": run.status,
            "created_at": run.created_at.isoformat()
            if getattr(run, "created_at", None)
            else None,
            "updated_at": run.updated_at.isoformat()
            if getattr(run, "updated_at", None)
            else None,
            "completed_at": run.completed_at.isoformat()
            if getattr(run, "completed_at", None)
            else None,
            "final_response": getattr(run, "final_response", None),
            "error_message": getattr(run, "error_message", None),
            "token_usage": getattr(run, "token_usage", None),
            "events": list(getattr(run, "events", []) or []),
        }

    async def get_instance(self, event, name, scope_type=None) -> SubAgentRuntimeResult:
        if not self.runtime_enabled:
            return self._runtime_disabled_result()
        if scope_type is not None:
            umo, resolved_scope_type, scope_id = await self.resolve_scope(
                event, scope_type
            )
            instance = await self.db.get_subagent_instance_by_name(
                umo=umo,
                scope_type=resolved_scope_type,
                scope_id=scope_id,
                name=name,
            )
            if instance is None:
                return SubAgentRuntimeResult.failure(
                    INSTANCE_NOT_FOUND,
                    "Sub-agent instance was not found.",
                )
            return SubAgentRuntimeResult.success(instance)

        umo, _, conversation_id = await self.resolve_scope(event, "conversation")
        conversation_instance = await self.db.get_subagent_instance_by_name(
            umo=umo,
            scope_type="conversation",
            scope_id=conversation_id,
            name=name,
        )
        session_instance = await self.db.get_subagent_instance_by_name(
            umo=umo,
            scope_type="session",
            scope_id=umo,
            name=name,
        )
        if conversation_instance and session_instance:
            return SubAgentRuntimeResult.failure(
                AMBIGUOUS_INSTANCE,
                "Sub-agent instance name exists in multiple scopes.",
            )
        instance = conversation_instance or session_instance
        if instance is None:
            return SubAgentRuntimeResult.failure(
                INSTANCE_NOT_FOUND,
                "Sub-agent instance was not found.",
            )
        return SubAgentRuntimeResult.success(instance)

    async def get_instance_status(
        self,
        event,
        name,
        scope_type=None,
    ) -> SubAgentRuntimeResult:
        loaded = await self.get_instance(event, name, scope_type=scope_type)
        if not loaded.ok:
            return loaded
        latest_run = await self.db.get_latest_subagent_background_run(
            loaded.data.instance_id
        )
        latest_run = await self._reconcile_background_run(latest_run)
        return SubAgentRuntimeResult.success(
            {
                "instance": loaded.data,
                "busy": self.is_instance_locked(loaded.data.instance_id),
                "background_run": self._background_run_payload(latest_run),
            }
        )

    async def create_instance(
        self,
        event,
        name,
        preset_name,
        scope_type=None,
        overrides=None,
    ) -> SubAgentRuntimeResult:
        if not self.runtime_enabled:
            return self._runtime_disabled_result()
        preset = self.presets.get(preset_name)
        if preset is None or preset.runtime_mode != "persistent":
            return SubAgentRuntimeResult.failure(
                PRESET_NOT_FOUND,
                "Persistent sub-agent preset was not found.",
            )

        overrides = overrides or {}
        umo, resolved_scope_type, scope_id = await self.resolve_scope(event, scope_type)
        scope_key = f"{umo}\0{resolved_scope_type}\0{scope_id}"
        create_lock = self._scope_create_locks.setdefault(scope_key, asyncio.Lock())
        async with create_lock:
            return await self._create_instance_locked(
                umo,
                resolved_scope_type,
                scope_id,
                name,
                preset,
                overrides,
            )

    async def _create_instance_locked(
        self,
        umo,
        resolved_scope_type,
        scope_id,
        name,
        preset,
        overrides,
    ) -> SubAgentRuntimeResult:
        existing = await self.db.get_subagent_instance_by_name(
            umo=umo,
            scope_type=resolved_scope_type,
            scope_id=scope_id,
            name=name,
        )
        if existing is not None:
            return SubAgentRuntimeResult.failure(
                INSTANCE_EXISTS,
                "Sub-agent instance already exists in this scope.",
            )

        current_instances = await self.db.list_subagent_instances(
            umo=umo,
            scope_type=resolved_scope_type,
            scope_id=scope_id,
        )
        if len(current_instances) >= self.max_instances_per_scope:
            return SubAgentRuntimeResult.failure(
                MAX_INSTANCES_REACHED,
                "Maximum sub-agent instances reached for this scope.",
            )

        tools = overrides["tools"] if "tools" in overrides else preset.tools
        invalid_tool = self._first_invalid_tool(tools)
        if invalid_tool is not None:
            return SubAgentRuntimeResult.failure(
                INVALID_TOOL,
                "Tool override is not active or does not exist.",
                {"tool": invalid_tool},
            )

        skills = overrides["skills"] if "skills" in overrides else preset.skills
        invalid_skill = self._first_invalid_skill(skills)
        if invalid_skill is not None:
            return SubAgentRuntimeResult.failure(
                INVALID_SKILL,
                "Skill override is not active or does not exist.",
                {"skill": invalid_skill},
            )

        system_prompt = overrides.get(
            "system_prompt",
            overrides.get("instructions", preset.instructions),
        )
        try:
            instance = await self.db.create_subagent_instance(
                umo=umo,
                scope_type=resolved_scope_type,
                scope_id=scope_id,
                name=name,
                preset_name=preset.name,
                provider_id=overrides.get("provider_id", preset.provider_id),
                persona_id=overrides.get("persona_id", preset.persona_id),
                system_prompt=system_prompt,
                system_prompt_delta=overrides.get("system_prompt_delta"),
                tools=list(tools) if tools is not None else None,
                skills=list(skills) if skills is not None else None,
                history=[],
                max_persisted_turns=self.max_persisted_turns,
                max_persisted_tokens=self.max_persisted_tokens,
            )
        except IntegrityError:
            return SubAgentRuntimeResult.failure(
                INSTANCE_EXISTS,
                "Sub-agent instance already exists in this scope.",
            )
        return SubAgentRuntimeResult.success(instance)

    async def create_instance_from_persona(
        self,
        event,
        name,
        persona_id,
        scope_type=None,
        overrides=None,
    ) -> SubAgentRuntimeResult:
        if not self.runtime_enabled:
            return self._runtime_disabled_result()
        persona_id = str(persona_id or "").strip()
        if not persona_id:
            return SubAgentRuntimeResult.failure(
                PRESET_NOT_FOUND,
                "Persona was not found.",
            )
        persona = self.persona_mgr.get_persona_v3_by_id(persona_id)
        if persona is None:
            return SubAgentRuntimeResult.failure(
                PRESET_NOT_FOUND,
                "Persona was not found.",
            )

        preset = SubAgentPreset(
            name=f"persona:{persona_id}",
            runtime_mode="persistent",
            instructions=self._persona_value(persona, "prompt")
            or self._persona_value(persona, "system_prompt"),
            provider_id=self._persona_value(persona, "provider_id"),
            persona_id=persona_id,
            tools=self._capability_value({}, persona, "tools"),
            skills=self._capability_value({}, persona, "skills"),
            begin_dialogs=list(
                self._persona_value(persona, "_begin_dialogs_processed")
                or self._persona_value(persona, "begin_dialogs")
                or []
            ),
        )

        overrides = overrides or {}
        umo, resolved_scope_type, scope_id = await self.resolve_scope(event, scope_type)
        scope_key = f"{umo}\0{resolved_scope_type}\0{scope_id}"
        create_lock = self._scope_create_locks.setdefault(scope_key, asyncio.Lock())
        async with create_lock:
            return await self._create_instance_locked(
                umo,
                resolved_scope_type,
                scope_id,
                name,
                preset,
                overrides,
            )

    async def update_instance(
        self,
        event,
        name,
        scope_type=None,
        updates=None,
    ) -> SubAgentRuntimeResult:
        loaded = await self.get_instance(event, name, scope_type=scope_type)
        if not loaded.ok:
            return loaded

        updates = dict(updates or {})
        invalid_fields = sorted(set(updates) - _MUTABLE_INSTANCE_FIELDS)
        if invalid_fields:
            return SubAgentRuntimeResult.failure(
                INVALID_UPDATE_FIELD,
                "Sub-agent instance update contains invalid fields.",
                {"fields": invalid_fields},
            )
        if "tools" in updates:
            invalid_tool = self._first_invalid_tool(updates["tools"])
            if invalid_tool is not None:
                return SubAgentRuntimeResult.failure(
                    INVALID_TOOL,
                    "Tool override is not active or does not exist.",
                    {"tool": invalid_tool},
                )
        if "skills" in updates:
            invalid_skill = self._first_invalid_skill(updates["skills"])
            if invalid_skill is not None:
                return SubAgentRuntimeResult.failure(
                    INVALID_SKILL,
                    "Skill override is not active or does not exist.",
                    {"skill": invalid_skill},
                )

        async with self._instance_lifecycle_lock(loaded.data.instance_id):
            if self.is_instance_locked(loaded.data.instance_id):
                return SubAgentRuntimeResult.failure(
                    INSTANCE_BUSY,
                    "Sub-agent instance is busy.",
                )

            updated = await self.db.update_subagent_instance(
                loaded.data.instance_id,
                **updates,
            )
        if updated is None:
            return SubAgentRuntimeResult.failure(
                INSTANCE_NOT_FOUND,
                "Sub-agent instance was not found.",
            )
        return SubAgentRuntimeResult.success(updated)

    async def reset_instance(
        self,
        event,
        name,
        scope_type=None,
    ) -> SubAgentRuntimeResult:
        loaded = await self.get_instance(event, name, scope_type=scope_type)
        if not loaded.ok:
            return loaded
        async with self._instance_lifecycle_lock(loaded.data.instance_id):
            if self.is_instance_locked(loaded.data.instance_id):
                return SubAgentRuntimeResult.failure(
                    INSTANCE_BUSY,
                    "Sub-agent instance is busy.",
                )
            return await self.save_history(
                loaded.data,
                [],
                token_usage=0,
                begin_dialogs_injected=False,
            )

    async def delete_instance(
        self,
        event,
        name,
        scope_type=None,
    ) -> SubAgentRuntimeResult:
        loaded = await self.get_instance(event, name, scope_type=scope_type)
        if not loaded.ok:
            return loaded
        async with self._instance_lifecycle_lock(loaded.data.instance_id):
            if self.is_instance_locked(loaded.data.instance_id):
                return SubAgentRuntimeResult.failure(
                    INSTANCE_BUSY,
                    "Sub-agent instance is busy.",
                )
            await self.db.delete_subagent_instance(loaded.data.instance_id)
        return SubAgentRuntimeResult.success(loaded.data)

    async def delete_instance_by_id(self, instance_id) -> SubAgentRuntimeResult:
        async with self._instance_lifecycle_lock(instance_id):
            if self.is_instance_locked(instance_id):
                return SubAgentRuntimeResult.failure(
                    INSTANCE_BUSY,
                    "Sub-agent instance is busy.",
                )
            await self.db.delete_subagent_instance(instance_id)
        return SubAgentRuntimeResult.success({"instance_id": instance_id})

    async def run_instance(
        self,
        event,
        name,
        input_text,
        image_urls=None,
        scope_type=None,
        background_task=False,
        tool_call_timeout=120,
    ) -> SubAgentRuntimeResult:
        loaded = await self.get_instance(event, name, scope_type=scope_type)
        if not loaded.ok:
            return loaded

        instance = loaded.data
        async with self._instance_lifecycle_lock(instance.instance_id):
            instance = await self.db.get_subagent_instance_by_id(instance.instance_id)
            if instance is None:
                return SubAgentRuntimeResult.failure(
                    INSTANCE_NOT_FOUND,
                    "Sub-agent instance was not found.",
                )

            lock = self.try_acquire_instance_lock(instance.instance_id)
            if not lock.ok:
                return lock

            try:
                messages = list(instance.history or [])
                begin_dialogs_injected = bool(instance.begin_dialogs_injected)
                if not begin_dialogs_injected:
                    preset = self.presets.get(instance.preset_name)
                    if preset is not None and preset.begin_dialogs:
                        messages.extend(list(preset.begin_dialogs))
                    begin_dialogs_injected = True
                messages.append({"role": "user", "content": input_text})

                if background_task:
                    plugin_context = None
                    if hasattr(event, "get_extra"):
                        plugin_context = event.get_extra("subagent_runtime_context")
                    if plugin_context is None:
                        plugin_context = getattr(event, "context", None)
                    if plugin_context is None:
                        await lock.__aexit__(None, None, None)
                        return SubAgentRuntimeResult.failure(
                            SUBAGENT_EXECUTION_FAILED,
                            "Sub-agent runtime execution context is not available.",
                        )

                    background_run = None
                    try:
                        background_run = await self.db.create_subagent_background_run(
                            instance_id=instance.instance_id,
                            umo=instance.umo,
                            scope_type=instance.scope_type,
                            scope_id=instance.scope_id,
                            instance_name=instance.name,
                            preset_name=instance.preset_name,
                            status="queued",
                            input_text=input_text,
                            image_urls=list(image_urls or []),
                            events=[
                                self._background_event(
                                    "queued",
                                    "Background run queued.",
                                )
                            ],
                        )
                        wake_context = ContextWrapper(
                            context=SimpleNamespace(context=plugin_context, event=event),
                            tool_call_timeout=tool_call_timeout,
                        )
                        task = asyncio.create_task(
                            self._run_instance_background(
                                lock=lock,
                                task_id=background_run.task_id,
                                wake_context=wake_context,
                                event=event,
                                instance=instance,
                                messages=messages,
                                input_text=input_text,
                                image_urls=list(image_urls or []),
                                begin_dialogs_injected=begin_dialogs_injected,
                            )
                        )
                    except BaseException as exc:
                        error_message = str(exc) or exc.__class__.__name__
                        if isinstance(exc, asyncio.CancelledError):
                            error_message = (
                                "Background run was cancelled before scheduling."
                            )
                        try:
                            if background_run is not None:
                                try:
                                    await self.db.update_subagent_background_run(
                                        background_run.task_id,
                                        status="failed",
                                        error_message=error_message,
                                        completed_at=datetime.now(timezone.utc),
                                    )
                                    await self._safe_append_background_run_event(
                                        background_run.task_id,
                                        "failed",
                                        f"Background run failed: {error_message}",
                                    )
                                except Exception:
                                    logger.warning(
                                        "Failed to record background submission failure for "
                                        f"instance '{instance.name}':\n"
                                        f"{traceback.format_exc()}"
                                    )
                        finally:
                            await lock.__aexit__(None, None, None)
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                        logger.error(
                            f"Sub-agent background submission failed for instance '{instance.name}':\n{traceback.format_exc()}"
                        )
                        return SubAgentRuntimeResult.failure(
                            SUBAGENT_EXECUTION_FAILED,
                            "Sub-agent execution failed.",
                        )

                    self._track_background_task(background_run.task_id, task)
                    return SubAgentRuntimeResult.success(
                        {
                            "background_task": True,
                            "task_id": background_run.task_id,
                            "status": "queued",
                            "metadata": self._run_metadata(instance),
                        }
                    )
            except BaseException as exc:
                await lock.__aexit__(None, None, None)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                logger.error(
                    f"Sub-agent setup failed for instance '{instance.name}':\n{traceback.format_exc()}"
                )
                return SubAgentRuntimeResult.failure(
                    SUBAGENT_EXECUTION_FAILED,
                    "Sub-agent execution failed.",
                )

        async with lock:
            try:
                execution = await self._execute_instance(
                    event,
                    instance,
                    messages,
                    input_text,
                    image_urls,
                )
            except Exception:
                logger.error(
                    f"Sub-agent execution failed for instance '{instance.name}':\n{traceback.format_exc()}"
                )
                return SubAgentRuntimeResult.failure(
                    SUBAGENT_EXECUTION_FAILED,
                    "Sub-agent execution failed.",
                )
            final_response = execution.get("final_response", "")
            history = execution.get("history", messages)
            token_usage = execution.get(
                "token_usage", getattr(instance, "token_usage", 0)
            )

            saved = await self.save_history(
                instance,
                history,
                token_usage=token_usage,
                begin_dialogs_injected=begin_dialogs_injected,
            )
            if not saved.ok:
                return saved

        return SubAgentRuntimeResult.success(
            {
                "final_response": final_response,
                "metadata": self._run_metadata(saved.data),
            }
        )

    async def _run_instance_background(
        self,
        *,
        lock: _InstanceLockResult,
        task_id: str,
        wake_context: ContextWrapper[Any],
        event,
        instance,
        messages,
        input_text,
        image_urls,
        begin_dialogs_injected,
    ) -> None:
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

        wake_result_text = ""
        wake_extra_fields = {
            "instance_id": instance.instance_id,
            "status": "failed",
        }
        cancelled_error: asyncio.CancelledError | None = None
        history_saved = False
        try:
            await self.db.update_subagent_background_run(task_id, status="running")
            await self._safe_append_background_run_event(
                task_id,
                "started",
                "Background run started.",
            )

            execution = await self._execute_instance(
                event,
                instance,
                messages,
                input_text,
                image_urls,
                agent_hooks=_BackgroundRunHooks(self, task_id),
            )
            final_response = execution.get("final_response", "")
            history = execution.get("history", messages)
            token_usage = execution.get(
                "token_usage", getattr(instance, "token_usage", 0)
            )

            saved = await self.save_history(
                instance,
                history,
                token_usage=token_usage,
                begin_dialogs_injected=begin_dialogs_injected,
            )
            if not saved.ok:
                raise RuntimeError(
                    saved.error.message if saved.error else "save failed"
                )
            history_saved = True

            wake_result_text = final_response
            wake_extra_fields = {
                "instance_id": instance.instance_id,
                "status": "completed",
                "final_response": final_response,
                "token_usage": token_usage,
            }
            completed_at = datetime.now(timezone.utc)
            completed_override = {
                "status": "completed",
                "final_response": final_response,
                "error_message": None,
                "token_usage": token_usage,
                "completed_at": completed_at,
                "updated_at": completed_at,
            }

            try:
                await self.db.update_subagent_background_run(
                    task_id,
                    status="completed",
                    final_response=final_response,
                    error_message=None,
                    token_usage=token_usage,
                    completed_at=completed_at,
                )
                self._background_run_terminal_overrides.pop(task_id, None)
            except asyncio.CancelledError as exc:
                cancelled_error = exc
                self._background_run_terminal_overrides[task_id] = completed_override
                try:
                    await self.db.update_subagent_background_run(
                        task_id,
                        final_response=final_response,
                        error_message=None,
                        token_usage=token_usage,
                        completed_at=completed_at,
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist durable completion markers for background "
                        f"run '{task_id}' on instance '{instance.name}' after the "
                        "completed status write was cancelled."
                    )
                logger.warning(
                    "Cancelled while persisting completed status for background run "
                    f"'{task_id}' on instance '{instance.name}' after history was "
                    f"saved; preserving successful runtime state.\n{traceback.format_exc()}"
                )
            except Exception:
                self._background_run_terminal_overrides[task_id] = completed_override
                try:
                    await self.db.update_subagent_background_run(
                        task_id,
                        final_response=final_response,
                        error_message=None,
                        token_usage=token_usage,
                        completed_at=completed_at,
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist durable completion markers for background "
                        f"run '{task_id}' on instance '{instance.name}' after the "
                        "completed status write failed."
                    )
                logger.warning(
                    "Failed to persist completed status for background run "
                    f"'{task_id}' on instance '{instance.name}' after history was "
                    f"saved; preserving successful runtime state.\n{traceback.format_exc()}"
                )

            # Never downgrade a durably completed run if best-effort finalization fails.
            try:
                await self._append_background_run_event(
                    task_id,
                    "completed",
                    "Background run completed.",
                )
            except Exception:
                logger.warning(
                    "Failed to append completed event for background run "
                    f"'{task_id}' on instance '{instance.name}':\n"
                    f"{traceback.format_exc()}"
                )
        except asyncio.CancelledError as exc:
            cancelled_error = exc
            if history_saved:
                logger.warning(
                    "Sub-agent background execution was cancelled after history was "
                    "saved for instance "
                    f"'{instance.name}' (task '{task_id}')."
                )
            else:
                error_message = "Background run was cancelled."
                await self._best_effort_mark_background_run_failed(
                    task_id,
                    error_message,
                )
                wake_result_text = error_message
                wake_extra_fields = {
                    "instance_id": instance.instance_id,
                    "status": "failed",
                    "error_message": error_message,
                }
        except Exception as exc:
            error_message = str(exc)
            logger.error(
                f"Sub-agent background execution failed for instance '{instance.name}':\n{traceback.format_exc()}"
            )
            if history_saved:
                logger.warning(
                    "Sub-agent background execution hit a post-save error for "
                    f"instance '{instance.name}' (task '{task_id}'); preserving the "
                    "successful runtime state."
                )
            else:
                await self._best_effort_mark_background_run_failed(
                    task_id,
                    error_message,
                )
                wake_result_text = error_message
                wake_extra_fields = {
                    "instance_id": instance.instance_id,
                    "status": "failed",
                    "error_message": error_message,
                }
        finally:
            await lock.__aexit__(None, None, None)

        try:
            await FunctionToolExecutor._wake_main_agent_for_background_result(
                wake_context,
                task_id=task_id,
                tool_name="run_subagent",
                result_text=wake_result_text,
                tool_args={
                    "name": instance.name,
                    "input": input_text,
                    "image_urls": list(image_urls or []),
                    "background_task": True,
                },
                note=f"Background sub-agent run for {instance.name} finished.",
                summary_name=instance.name,
                extra_result_fields=wake_extra_fields,
            )
        except Exception:
            logger.error(
                f"Sub-agent background wake-up failed for instance '{instance.name}':\n{traceback.format_exc()}"
            )
        if cancelled_error is not None:
            raise cancelled_error

    async def _execute_instance(
        self,
        event,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        from astrbot.core.astr_agent_tool_exec import execute_persistent_subagent

        return await execute_persistent_subagent(
            event,
            instance,
            messages,
            input_text,
            image_urls=image_urls,
            agent_hooks=agent_hooks,
        )

    def prune_history_for_persistence(
        self,
        messages,
        max_persisted_turns=None,
        max_persisted_tokens=None,
    ):
        if max_persisted_turns is None:
            pruned = list(messages)
        else:
            pruned = self._prune_history_by_turns(messages, max_persisted_turns)

        if not isinstance(max_persisted_tokens, int) or max_persisted_tokens <= 0:
            return pruned
        return self._prune_history_by_tokens(pruned, max_persisted_tokens)

    def _prune_history_by_turns(self, messages, max_persisted_turns):
        leading_system_messages = []
        non_system_start = 0
        for index, message in enumerate(messages):
            if message.get("role") != "system":
                non_system_start = index
                break
            leading_system_messages.append(message)
        else:
            return list(leading_system_messages)

        non_system_messages = messages[non_system_start:]
        keep_count = max_persisted_turns * 2
        if keep_count <= 0:
            return list(leading_system_messages)
        return list(leading_system_messages) + list(non_system_messages[-keep_count:])

    def _prune_history_by_tokens(self, messages, max_persisted_tokens):
        message_models = []
        for message in messages:
            try:
                message_models.append(
                    message
                    if isinstance(message, Message)
                    else Message.model_validate(message)
                )
            except Exception:
                return list(messages)

        token_counter = EstimateTokenCounter()
        truncator = ContextTruncator()
        fixed_messages = truncator.fix_messages(message_models)
        system_messages, non_system_messages = truncator._split_system_rest(
            fixed_messages
        )

        while (
            len(non_system_messages) > 2
            and token_counter.count_tokens(system_messages + non_system_messages)
            > max_persisted_tokens
        ):
            non_system_messages = non_system_messages[2:]
            first_user_index = next(
                (
                    idx
                    for idx, msg in enumerate(non_system_messages)
                    if msg.role == "user"
                ),
                0,
            )
            non_system_messages = non_system_messages[first_user_index:]
            non_system_messages = truncator.fix_messages(
                system_messages + non_system_messages
            )[len(system_messages) :]

        return [
            message.model_dump(exclude_none=True)
            for message in system_messages + non_system_messages
        ]

    async def save_history(
        self,
        instance,
        messages,
        token_usage,
        begin_dialogs_injected,
    ) -> SubAgentRuntimeResult:
        history = self.prune_history_for_persistence(
            messages,
            max_persisted_turns=instance.max_persisted_turns,
            max_persisted_tokens=instance.max_persisted_tokens,
        )
        saved = await self.db.save_subagent_history(
            instance.instance_id,
            history=history,
            token_usage=token_usage,
            begin_dialogs_injected=begin_dialogs_injected,
            expected_version=instance.version,
        )
        if saved is None:
            return SubAgentRuntimeResult.failure(
                VERSION_CONFLICT,
                "Sub-agent instance history version conflict.",
            )
        return SubAgentRuntimeResult.success(saved)

    def is_instance_locked(self, instance_id) -> bool:
        return instance_id in self._instance_locks

    def try_acquire_instance_lock(self, instance_id) -> _InstanceLockResult:
        if self.is_instance_locked(instance_id):
            return _InstanceLockResult(self, instance_id, ok=False)
        self._instance_locks[instance_id] = True
        return _InstanceLockResult(self, instance_id, ok=True)

    def _instance_lifecycle_lock(self, instance_id) -> asyncio.Lock:
        return self._instance_lifecycle_locks.setdefault(instance_id, asyncio.Lock())

    def _first_invalid_tool(self, tools) -> str | None:
        if tools is None:
            return None
        tool_set = self.tool_mgr.get_full_tool_set()
        management_tool_names = self._subagent_runtime_management_tool_names()
        for tool in tools or []:
            if tool in management_tool_names:
                return tool
            registered_tool = tool_set.get_tool(tool)
            if registered_tool is None and hasattr(self.tool_mgr, "get_func"):
                registered_tool = self.tool_mgr.get_func(tool)
            if registered_tool is None or not getattr(registered_tool, "active", True):
                return tool
        return None

    @staticmethod
    def _subagent_runtime_management_tool_names() -> set[str]:
        try:
            from astrbot.core.tools.subagent_runtime_tools import (
                SUBAGENT_RUNTIME_MANAGEMENT_TOOLS,
            )
        except Exception:
            return set()
        return {tool_cls().name for tool_cls in SUBAGENT_RUNTIME_MANAGEMENT_TOOLS}

    def _first_invalid_skill(self, skills) -> str | None:
        if not skills:
            return None
        if self.skill_manager is None:
            return skills[0]

        active_skills = self.skill_manager.list_skills(active_only=True)
        active_names = {self._skill_name(skill) for skill in active_skills}
        for skill in skills:
            if skill not in active_names:
                return skill
        return None

    @staticmethod
    def _persona_value(persona, key):
        if persona is None:
            return None
        if isinstance(persona, dict):
            return persona.get(key)
        return getattr(persona, key, None)

    @staticmethod
    def _skill_name(skill):
        if isinstance(skill, str):
            return skill
        if isinstance(skill, dict):
            return skill.get("name") or skill.get("id")
        return getattr(skill, "name", None) or getattr(skill, "id", None)

    def _capability_value(self, agent, persona, key):
        if persona is not None:
            value = self._persona_value(persona, key)
            return None if value is None else list(value)
        if key not in agent:
            return []
        value = agent.get(key)
        return None if value is None else list(value)

    @staticmethod
    def _run_metadata(instance) -> dict[str, Any]:
        return {
            "instance_id": instance.instance_id,
            "name": instance.name,
            "preset_name": instance.preset_name,
            "scope_type": instance.scope_type,
            "scope_id": instance.scope_id,
            "version": instance.version,
            "token_usage": getattr(instance, "token_usage", 0),
        }

    @staticmethod
    def _runtime_disabled_result() -> SubAgentRuntimeResult:
        return SubAgentRuntimeResult.failure(
            RUNTIME_DISABLED,
            "Persistent sub-agent runtime is disabled.",
        )

    @staticmethod
    async def _maybe_await(value):
        if inspect.isawaitable(value):
            return await value
        return value

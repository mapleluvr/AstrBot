import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.subagent_runtime import (
    AMBIGUOUS_INSTANCE,
    INSTANCE_BUSY,
    INSTANCE_EXISTS,
    INSTANCE_NOT_FOUND,
    INVALID_SKILL,
    INVALID_TOOL,
    MAX_INSTANCES_REACHED,
    PRESET_NOT_FOUND,
    VERSION_CONFLICT,
    SubAgentRuntimeManager,
    normalize_subagent_orchestrator_config,
)


class FakeDB:
    def __init__(self):
        self.instances = []
        self.background_runs = []
        self.next_id = 1
        self.save_returns_none = False
        self.update_returns_none = False
        self.updated_instances = []
        self.deleted_instances = []
        self.deleted_sessions = []
        self.deleted_conversations = []
        self.create_error = None
        self.conversations_by_id = {}

    async def create_subagent_instance(self, **kwargs):
        if self.create_error is not None:
            raise self.create_error
        instance = SimpleNamespace(
            instance_id=f"instance-{self.next_id}",
            version=1,
            begin_dialogs_injected=False,
            **kwargs,
        )
        self.next_id += 1
        self.instances.append(instance)
        return instance

    async def get_subagent_instance_by_name(self, *, umo, scope_type, scope_id, name):
        for instance in self.instances:
            if (
                instance.umo == umo
                and instance.scope_type == scope_type
                and instance.scope_id == scope_id
                and instance.name == name
            ):
                return instance
        return None

    async def get_subagent_instance_by_id(self, instance_id):
        for instance in self.instances:
            if instance.instance_id == instance_id:
                return instance
        return None

    async def list_subagent_instances(self, *, umo, scope_type=None, scope_id=None):
        return [
            instance
            for instance in self.instances
            if instance.umo == umo
            and (scope_type is None or instance.scope_type == scope_type)
            and (scope_id is None or instance.scope_id == scope_id)
        ]

    async def save_subagent_history(
        self,
        instance_id,
        *,
        history,
        token_usage,
        begin_dialogs_injected,
        expected_version,
    ):
        if self.save_returns_none:
            return None
        for instance in self.instances:
            if instance.instance_id == instance_id:
                if instance.version != expected_version:
                    return None
                instance.history = history
                instance.token_usage = token_usage
                instance.begin_dialogs_injected = begin_dialogs_injected
                instance.version += 1
                return instance
        return None

    async def update_subagent_instance(self, instance_id, **kwargs):
        self.updated_instances.append((instance_id, kwargs))
        if self.update_returns_none:
            return None
        for instance in self.instances:
            if instance.instance_id == instance_id:
                for key, value in kwargs.items():
                    setattr(instance, key, value)
                return instance
        return None

    async def delete_subagent_instance(self, instance_id):
        self.deleted_instances.append(instance_id)
        self.instances = [
            instance
            for instance in self.instances
            if instance.instance_id != instance_id
        ]

    async def delete_subagent_instances_for_session(self, umo):
        self.deleted_sessions.append(umo)

    async def delete_subagent_instances_for_conversation(self, conversation_id):
        self.deleted_conversations.append(conversation_id)

    async def get_conversation_by_id(self, cid):
        return self.conversations_by_id.get(cid)

    async def create_subagent_background_run(self, **kwargs):
        now = datetime.now(timezone.utc)
        payload = {
            "task_id": f"task-{len(self.background_runs) + 1}",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "final_response": None,
            "error_message": None,
            "token_usage": None,
            "events": [],
        }
        payload.update(kwargs)
        run = SimpleNamespace(**payload)
        self.background_runs.append(run)
        return run

    async def get_subagent_background_run(self, task_id):
        for run in self.background_runs:
            if run.task_id == task_id:
                return run
        return None

    async def get_latest_subagent_background_run(self, instance_id):
        matches = [
            run for run in self.background_runs if run.instance_id == instance_id
        ]
        return matches[-1] if matches else None

    async def update_subagent_background_run(self, task_id, **kwargs):
        run = await self.get_subagent_background_run(task_id)
        if run is None:
            return None
        for key, value in kwargs.items():
            setattr(run, key, value)
        if "updated_at" not in kwargs:
            run.updated_at = datetime.now(timezone.utc)
        return run

    async def append_subagent_background_run_event(
        self,
        task_id,
        event,
        *,
        max_events=10,
    ):
        run = await self.get_subagent_background_run(task_id)
        if run is None:
            return None
        events = list(getattr(run, "events", []) or [])
        events.append(event)
        if max_events > 0:
            events = events[-max_events:]
        run.events = events
        run.updated_at = datetime.now(timezone.utc)
        return run


class FakePersonaManager:
    def __init__(self):
        self.personas = {}

    def get_persona_v3_by_id(self, persona_id):
        return self.personas.get(persona_id)


class FakeConversationManager:
    def __init__(self):
        self.current = {}
        self.created = []

    def get_curr_conversation_id(self, umo):
        return self.current.get(umo)

    def new_conversation(self, umo, platform_id):
        conversation_id = f"conv-{len(self.created) + 1}"
        self.current[umo] = conversation_id
        self.created.append((umo, platform_id, conversation_id))
        return conversation_id


class AsyncFakeConversationManager(FakeConversationManager):
    async def get_curr_conversation_id(self, umo):
        return super().get_curr_conversation_id(umo)

    async def new_conversation(self, umo, platform_id):
        return super().new_conversation(umo, platform_id)


class FakeToolSet:
    def __init__(self, tools):
        self.tools = dict(tools)

    def get_tool(self, name):
        active = self.tools.get(name)
        if active is None:
            return None
        return SimpleNamespace(active=active)


class FakeToolManager:
    def __init__(self, tools=None, builtin_tools=None):
        self.tools = {}
        for item in tools or []:
            if isinstance(item, tuple):
                name, active = item
            else:
                name, active = item, True
            self.tools[name] = active
        self.builtin_tools = {}
        for item in builtin_tools or []:
            if isinstance(item, tuple):
                name, active = item
            else:
                name, active = item, True
            self.builtin_tools[name] = active

    def get_full_tool_set(self):
        return FakeToolSet(self.tools)

    def get_func(self, name):
        if name in self.tools:
            return SimpleNamespace(active=self.tools[name])
        if name in self.builtin_tools:
            return SimpleNamespace(active=self.builtin_tools[name])
        return None


class FakeSkillManager:
    def __init__(self, skills):
        self.skills = skills

    def list_skills(self, active_only=True):
        assert active_only is True
        return self.skills


class FakeEvent:
    unified_msg_origin = "telegram:FriendMessage:user1"

    def get_platform_id(self):
        return "telegram"


def manager(config=None, *, tools=None, builtin_tools=None, skills=None):
    return SubAgentRuntimeManager(
        FakeDB(),
        FakeToolManager(tools, builtin_tools),
        FakePersonaManager(),
        FakeConversationManager(),
        config=config,
        skill_manager=FakeSkillManager(skills or []),
    )


class SlowCreateDB(FakeDB):
    async def create_subagent_instance(self, **kwargs):
        await asyncio.sleep(0)
        return await super().create_subagent_instance(**kwargs)


def manager_with_db(db, config=None, *, tools=None, builtin_tools=None, skills=None):
    return SubAgentRuntimeManager(
        db,
        FakeToolManager(tools, builtin_tools),
        FakePersonaManager(),
        FakeConversationManager(),
        config=config,
        skill_manager=FakeSkillManager(skills or []),
    )


def enabled_runtime_config(config):
    config = dict(config)
    runtime = dict(config.get("runtime", {}))
    runtime["enable"] = True
    config["runtime"] = runtime
    return config


@pytest.mark.asyncio
async def test_cleanup_for_session_deletes_persisted_session_instances():
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(
        event,
        "agent",
        "researcher",
        scope_type="session",
    )

    await runtime.cleanup_for_session(event.unified_msg_origin)

    assert runtime.db.deleted_instances == [created.data.instance_id]
    assert runtime.db.instances == []


@pytest.mark.asyncio
async def test_cleanup_for_conversation_deletes_persisted_conversation_instances():
    db = FakeDB()
    runtime = manager_with_db(
        db,
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        ),
    )
    event = FakeEvent()
    db.conversations_by_id["conv-1"] = SimpleNamespace(user_id=event.unified_msg_origin)
    created = await runtime.create_instance(event, "agent", "researcher")

    await runtime.cleanup_for_conversation("conv-1")

    assert runtime.db.deleted_instances == [created.data.instance_id]
    assert runtime.db.instances == []


@pytest.mark.asyncio
async def test_cleanup_for_session_skips_busy_instances_and_deletes_idle_ones():
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    busy = await runtime.create_instance(
        event, "busy", "researcher", scope_type="session"
    )
    idle = await runtime.create_instance(
        event, "idle", "researcher", scope_type="session"
    )

    lock = runtime.try_acquire_instance_lock(busy.data.instance_id)
    assert lock.ok is True
    try:
        await runtime.cleanup_for_session(event.unified_msg_origin)
    finally:
        await lock.__aexit__(None, None, None)

    assert runtime.db.deleted_instances == [idle.data.instance_id]
    assert {instance.instance_id for instance in runtime.db.instances} == {
        busy.data.instance_id
    }


@pytest.mark.asyncio
async def test_cleanup_for_conversation_skips_busy_instances_and_deletes_idle_ones():
    db = FakeDB()
    runtime = manager_with_db(
        db,
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        ),
    )
    event = FakeEvent()
    db.conversations_by_id["conv-1"] = SimpleNamespace(user_id=event.unified_msg_origin)
    busy = await runtime.create_instance(event, "busy", "researcher")
    idle = await runtime.create_instance(event, "idle", "researcher")

    lock = runtime.try_acquire_instance_lock(busy.data.instance_id)
    assert lock.ok is True
    try:
        await runtime.cleanup_for_conversation("conv-1")
    finally:
        await lock.__aexit__(None, None, None)

    assert runtime.db.deleted_instances == [idle.data.instance_id]
    assert {instance.instance_id for instance in runtime.db.instances} == {
        busy.data.instance_id
    }


@pytest.mark.asyncio
async def test_create_instance_rejects_when_runtime_is_disabled():
    runtime = manager()
    runtime.reload_from_config(
        {
            "runtime": {"enable": False},
            "agents": [{"name": "researcher", "runtime_mode": "persistent"}],
        }
    )

    result = await runtime.create_instance(FakeEvent(), "agent", "researcher")

    assert result.ok is False
    assert result.error.error_code == "runtime_disabled"


@pytest.mark.asyncio
async def test_list_instances_rejects_when_runtime_is_disabled():
    runtime = manager()
    runtime.reload_from_config(
        {
            "runtime": {"enable": False},
            "agents": [{"name": "researcher", "runtime_mode": "persistent"}],
        }
    )

    result = await runtime.list_instances(FakeEvent())

    assert result.ok is False
    assert result.error.error_code == "runtime_disabled"


def test_reload_from_config_creates_persistent_presets_from_persona():
    runtime = manager()
    runtime.persona_mgr.personas["persona-a"] = SimpleNamespace(
        prompt="Persona prompt",
        tools=["persona_tool"],
        skills=["persona_skill"],
        _begin_dialogs_processed=[{"role": "assistant", "content": "hello"}],
    )

    runtime.reload_from_config(
        {
            "agents": [
                {
                    "name": "researcher",
                    "runtime_mode": "persistent",
                    "persona_id": "persona-a",
                    "provider_id": "provider-a",
                    "public_description": "Research helper",
                }
            ]
        }
    )

    preset = runtime.list_presets(runtime_mode="persistent")[0]
    assert preset.name == "researcher"
    assert preset.runtime_mode == "persistent"
    assert preset.instructions == "Persona prompt"
    assert preset.tools == ["persona_tool"]
    assert preset.skills == ["persona_skill"]
    assert preset.begin_dialogs == [{"role": "assistant", "content": "hello"}]
    assert runtime.runtime_enabled is False
    assert runtime.max_instances_per_scope == 8
    assert runtime.max_persisted_turns == 20
    assert runtime.max_persisted_tokens is None


def test_reload_from_config_uses_existing_subagent_keys():
    runtime = manager()

    runtime.reload_from_config(
        {
            "agents": [
                {
                    "name": "disabled",
                    "enabled": False,
                    "runtime_mode": "persistent",
                },
                {
                    "name": "writer",
                    "runtime_mode": "persistent",
                    "system_prompt": "Write carefully.",
                    "tools": ["tool_a"],
                    "skills": ["skill_a"],
                },
            ]
        }
    )

    presets = runtime.list_presets(runtime_mode="persistent")
    presets_by_name = {preset.name: preset for preset in presets}

    assert set(presets_by_name) == {"writer", "agent_group_summary"}
    assert presets_by_name["writer"].instructions == "Write carefully."
    assert presets_by_name["writer"].tools == ["tool_a"]
    assert presets_by_name["writer"].skills == ["skill_a"]
    assert presets_by_name["agent_group_summary"].tools == []
    assert presets_by_name["agent_group_summary"].skills == []


def test_summary_preset_backfill_only_checks_presence():
    raw_summary = {
        "name": "agent_group_summary",
        "enabled": False,
        "runtime_mode": "handoff",
        "system_prompt": "User edited summary prompt.",
        "tools": None,
        "custom_user_field": {"kept": True},
    }

    normalized = normalize_subagent_orchestrator_config({"agents": [raw_summary]})

    assert normalized["agents"] == [raw_summary]


def test_reload_from_config_defaults_to_handoff_and_skips_invalid_agents():
    runtime = manager()

    runtime.reload_from_config(
        {
            "agents": [
                "not-a-dict",
                {"runtime_mode": "persistent"},
                {"name": "legacy"},
            ]
        }
    )

    presets = runtime.list_presets()

    presets_by_name = {preset.name: preset for preset in presets}
    assert set(presets_by_name) == {"legacy", "agent_group_summary"}
    assert presets_by_name["legacy"].runtime_mode == "handoff"
    assert presets_by_name["agent_group_summary"].runtime_mode == "persistent"


def test_reload_from_config_preserves_none_capability_semantics_from_persona():
    runtime = manager()
    runtime.persona_mgr.personas["persona-all"] = {
        "prompt": "Persona prompt",
        "tools": None,
        "skills": None,
        "_begin_dialogs_processed": [],
    }

    runtime.reload_from_config(
        {
            "agents": [
                {
                    "name": "all_caps",
                    "runtime_mode": "persistent",
                    "persona_id": "persona-all",
                },
                {
                    "name": "no_caps",
                    "runtime_mode": "persistent",
                    "system_prompt": "No capabilities.",
                    "tools": [],
                    "skills": [],
                },
            ]
        }
    )

    presets = {preset.name: preset for preset in runtime.list_presets("persistent")}

    assert presets["all_caps"].tools is None
    assert presets["all_caps"].skills is None
    assert presets["no_caps"].tools == []
    assert presets["no_caps"].skills == []


@pytest.mark.asyncio
async def test_resolve_scope_defaults_to_conversation_and_creates_when_missing():
    runtime = manager()
    event = FakeEvent()

    umo, scope_type, scope_id = await runtime.resolve_scope(event)

    assert umo == event.unified_msg_origin
    assert scope_type == "conversation"
    assert scope_id == "conv-1"
    assert runtime.conversation_manager.created == [
        (event.unified_msg_origin, "telegram", "conv-1")
    ]


@pytest.mark.asyncio
async def test_resolve_scope_supports_async_conversation_manager():
    runtime = SubAgentRuntimeManager(
        FakeDB(),
        FakeToolManager(),
        FakePersonaManager(),
        AsyncFakeConversationManager(),
    )
    event = FakeEvent()

    umo, scope_type, scope_id = await runtime.resolve_scope(event)

    assert umo == event.unified_msg_origin
    assert scope_type == "conversation"
    assert scope_id == "conv-1"


@pytest.mark.asyncio
async def test_resolve_scope_session_uses_umo_as_scope_id():
    runtime = manager()
    event = FakeEvent()

    umo, scope_type, scope_id = await runtime.resolve_scope(event, scope_type="session")

    assert umo == event.unified_msg_origin
    assert scope_type == "session"
    assert scope_id == event.unified_msg_origin


@pytest.mark.asyncio
async def test_create_instance_rejects_duplicate_and_max_instances():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {
                "max_instances_per_scope": 1,
                "agents": [{"name": "researcher", "runtime_mode": "persistent"}],
            }
        )
    )
    event = FakeEvent()

    created = await runtime.create_instance(event, "agent", "researcher")
    duplicate = await runtime.create_instance(event, "agent", "researcher")
    maxed = await runtime.create_instance(event, "other", "researcher")

    assert created.ok is True
    assert duplicate.ok is False
    assert duplicate.error.error_code == INSTANCE_EXISTS
    assert maxed.ok is False
    assert maxed.error.error_code == MAX_INSTANCES_REACHED


@pytest.mark.asyncio
async def test_create_instance_rejects_missing_or_handoff_presets():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {
                "agents": [
                    {"name": "legacy", "runtime_mode": "handoff"},
                ]
            }
        )
    )
    event = FakeEvent()

    missing = await runtime.create_instance(event, "agent", "missing")
    handoff = await runtime.create_instance(event, "agent", "legacy")

    assert missing.ok is False
    assert missing.error.error_code == PRESET_NOT_FOUND
    assert handoff.ok is False
    assert handoff.error.error_code == PRESET_NOT_FOUND


@pytest.mark.asyncio
async def test_create_instance_from_persona_uses_persona_prompt_and_capabilities():
    runtime = manager(tools=["persona_tool"], skills=["persona_skill"])
    runtime.reload_from_config(enabled_runtime_config({"agents": []}))
    runtime.persona_mgr.personas["review_persona"] = SimpleNamespace(
        prompt="Persona prompt",
        tools=["persona_tool"],
        skills=["persona_skill"],
        _begin_dialogs_processed=[],
    )

    created = await runtime.create_instance_from_persona(
        FakeEvent(),
        "reviewer",
        "review_persona",
        overrides={"system_prompt_delta": "Group run instructions."},
    )

    assert created.ok is True
    assert created.data.name == "reviewer"
    assert created.data.persona_id == "review_persona"
    assert created.data.system_prompt == "Persona prompt"
    assert created.data.system_prompt_delta == "Group run instructions."
    assert created.data.tools == ["persona_tool"]
    assert created.data.skills == ["persona_skill"]


@pytest.mark.asyncio
async def test_create_instance_validates_tool_and_skill_overrides():
    runtime = manager(tools=["web_search"], skills=["summarize"])
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()

    invalid_tool = await runtime.create_instance(
        event, "bad-tool", "researcher", overrides={"tools": ["missing"]}
    )
    invalid_skill = await runtime.create_instance(
        event, "bad-skill", "researcher", overrides={"skills": ["missing"]}
    )
    created = await runtime.create_instance(
        event,
        "valid",
        "researcher",
        overrides={"tools": ["web_search"], "skills": ["summarize"]},
    )

    assert invalid_tool.error.error_code == INVALID_TOOL
    assert invalid_skill.error.error_code == INVALID_SKILL
    assert created.ok is True
    assert created.data.tools == ["web_search"]
    assert created.data.skills == ["summarize"]


@pytest.mark.asyncio
async def test_create_instance_preserves_none_capabilities_and_rejects_inactive_tools():
    runtime = manager(tools=[("active_tool", True), ("inactive_tool", False)])
    runtime.reload_from_config(
        enabled_runtime_config(
            {
                "agents": [
                    {
                        "name": "all_caps",
                        "runtime_mode": "persistent",
                        "tools": None,
                        "skills": None,
                    }
                ]
            }
        )
    )
    event = FakeEvent()

    created = await runtime.create_instance(event, "all", "all_caps")
    inactive = await runtime.create_instance(
        event,
        "inactive",
        "all_caps",
        overrides={"tools": ["inactive_tool"]},
    )

    assert created.ok is True
    assert created.data.tools is None
    assert created.data.skills is None
    assert inactive.ok is False
    assert inactive.error.error_code == INVALID_TOOL


@pytest.mark.asyncio
async def test_create_instance_accepts_active_builtin_tools_outside_full_tool_set():
    runtime = manager(builtin_tools=["astrbot_file_read_tool"])
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()

    created = await runtime.create_instance(
        event,
        "agent",
        "researcher",
        overrides={"tools": ["astrbot_file_read_tool"]},
    )

    assert created.ok is True
    assert created.data.tools == ["astrbot_file_read_tool"]


@pytest.mark.asyncio
async def test_create_instance_rejects_runtime_management_tool_overrides():
    runtime = manager(builtin_tools=["run_subagent"])
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()

    result = await runtime.create_instance(
        event,
        "agent",
        "researcher",
        overrides={"tools": ["run_subagent"]},
    )

    assert result.ok is False
    assert result.error.error_code == INVALID_TOOL
    assert result.error.details == {"tool": "run_subagent"}


@pytest.mark.asyncio
async def test_create_instance_persists_resolved_fields_and_limits():
    runtime = manager(tools=["web_search"], skills=["summarize"])
    runtime.reload_from_config(
        {
            "runtime": {
                "enable": True,
                "max_persisted_turns": 9,
                "max_persisted_tokens": 1234,
            },
            "agents": [
                {
                    "name": "researcher",
                    "runtime_mode": "persistent",
                    "provider_id": "provider-a",
                    "persona_id": "persona-a",
                    "system_prompt": "Preset prompt",
                    "tools": ["web_search"],
                    "skills": ["summarize"],
                }
            ],
        }
    )
    event = FakeEvent()

    created = await runtime.create_instance(
        event,
        "agent",
        "researcher",
        overrides={
            "provider_id": "provider-b",
            "system_prompt_delta": "Use citations.",
        },
    )

    assert created.ok is True
    assert created.data.provider_id == "provider-b"
    assert created.data.persona_id == "persona-a"
    assert created.data.system_prompt == "Preset prompt"
    assert created.data.system_prompt_delta == "Use citations."
    assert created.data.max_persisted_turns == 9
    assert created.data.max_persisted_tokens == 1234


@pytest.mark.asyncio
async def test_create_instance_accepts_system_prompt_override():
    runtime = manager()
    runtime.reload_from_config(
        {
            "runtime": {"enable": True},
            "agents": [
                {
                    "name": "researcher",
                    "runtime_mode": "persistent",
                    "system_prompt": "Preset prompt",
                }
            ],
        }
    )

    created = await runtime.create_instance(
        FakeEvent(),
        "agent",
        "researcher",
        overrides={"system_prompt": "Override prompt"},
    )

    assert created.ok is True
    assert created.data.system_prompt == "Override prompt"


@pytest.mark.asyncio
async def test_create_instance_converts_integrity_error_to_duplicate_result():
    db = FakeDB()
    db.create_error = IntegrityError("insert", {}, Exception("duplicate"))
    runtime = manager_with_db(db)
    runtime.reload_from_config(
        {
            "runtime": {"enable": True},
            "agents": [{"name": "researcher", "runtime_mode": "persistent"}],
        }
    )

    result = await runtime.create_instance(FakeEvent(), "agent", "researcher")

    assert result.ok is False
    assert result.error.error_code == INSTANCE_EXISTS


@pytest.mark.asyncio
async def test_create_instance_serializes_max_count_check_per_scope():
    runtime = manager_with_db(SlowCreateDB())
    runtime.reload_from_config(
        {
            "runtime": {"enable": True, "max_instances_per_scope": 1},
            "agents": [{"name": "researcher", "runtime_mode": "persistent"}],
        }
    )
    event = FakeEvent()

    first, second = await asyncio.gather(
        runtime.create_instance(event, "agent-a", "researcher"),
        runtime.create_instance(event, "agent-b", "researcher"),
    )

    assert [first.ok, second.ok].count(True) == 1
    failed = second if first.ok else first
    assert failed.error.error_code == MAX_INSTANCES_REACHED


@pytest.mark.asyncio
async def test_get_instance_reports_ambiguous_name_across_scopes():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()

    await runtime.create_instance(event, "agent", "researcher")
    await runtime.create_instance(event, "agent", "researcher", scope_type="session")

    result = await runtime.get_instance(event, "agent")

    assert result.ok is False
    assert result.error.error_code == AMBIGUOUS_INSTANCE


@pytest.mark.asyncio
async def test_list_instances_returns_conversation_and_session_instances():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    conversation = await runtime.create_instance(
        event, "conversation-agent", "researcher"
    )
    session = await runtime.create_instance(
        event,
        "session-agent",
        "researcher",
        scope_type="session",
    )

    instances = await runtime.list_instances(event)

    assert conversation.data in instances
    assert session.data in instances


@pytest.mark.asyncio
async def test_get_instance_success_explicit_scope_and_not_found():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(
        event,
        "agent",
        "researcher",
        scope_type="session",
    )

    loaded = await runtime.get_instance(event, "agent", scope_type="session")
    missing = await runtime.get_instance(event, "missing", scope_type="session")

    assert loaded.ok is True
    assert loaded.data == created.data
    assert missing.ok is False
    assert missing.error.error_code == INSTANCE_NOT_FOUND


@pytest.mark.asyncio
async def test_update_instance_validates_and_persists_allowed_updates():
    runtime = manager(tools=["search"], skills=["summarize"])
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    updated = await runtime.update_instance(
        event,
        "agent",
        updates={
            "provider_id": "provider-b",
            "tools": ["search"],
            "skills": ["summarize"],
        },
    )

    assert updated.ok is True
    assert updated.data.provider_id == "provider-b"
    assert updated.data.tools == ["search"]
    assert updated.data.skills == ["summarize"]
    assert runtime.db.updated_instances == [
        (
            created.data.instance_id,
            {
                "provider_id": "provider-b",
                "tools": ["search"],
                "skills": ["summarize"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_update_instance_accepts_active_builtin_tools_outside_full_tool_set():
    runtime = manager(builtin_tools=["astrbot_file_read_tool"])
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    await runtime.create_instance(event, "agent", "researcher")

    updated = await runtime.update_instance(
        event,
        "agent",
        updates={"tools": ["astrbot_file_read_tool"]},
    )

    assert updated.ok is True
    assert updated.data.tools == ["astrbot_file_read_tool"]


@pytest.mark.asyncio
async def test_update_instance_rejects_invalid_tool_and_skill_updates():
    runtime = manager(tools=["search"], skills=["summarize"])
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    await runtime.create_instance(event, "agent", "researcher")

    invalid_tool = await runtime.update_instance(
        event, "agent", updates={"tools": ["missing"]}
    )
    invalid_skill = await runtime.update_instance(
        event, "agent", updates={"skills": ["missing"]}
    )

    assert invalid_tool.ok is False
    assert invalid_tool.error.error_code == INVALID_TOOL
    assert invalid_skill.ok is False
    assert invalid_skill.error.error_code == INVALID_SKILL
    assert runtime.db.updated_instances == []


@pytest.mark.asyncio
async def test_update_instance_rejects_unsafe_fields_before_db_update():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    await runtime.create_instance(event, "agent", "researcher")

    invalid = await runtime.update_instance(
        event,
        "agent",
        updates={
            "provider_id": "provider-b",
            "name": "renamed",
            "history": [{"role": "user", "content": "tamper"}],
        },
    )

    assert invalid.ok is False
    assert invalid.error.error_code == "invalid_update_field"
    assert invalid.error.details == {"fields": ["history", "name"]}
    assert runtime.db.updated_instances == []


@pytest.mark.asyncio
async def test_mutations_return_busy_when_instance_is_locked():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")
    runtime.try_acquire_instance_lock(created.data.instance_id)

    update = await runtime.update_instance(
        event, "agent", updates={"provider_id": "provider-b"}
    )
    reset = await runtime.reset_instance(event, "agent")
    delete = await runtime.delete_instance(event, "agent")

    assert update.ok is False
    assert update.error.error_code == "instance_busy"
    assert reset.ok is False
    assert reset.error.error_code == "instance_busy"
    assert delete.ok is False
    assert delete.error.error_code == "instance_busy"
    assert runtime.db.updated_instances == []
    assert runtime.db.deleted_instances == []


@pytest.mark.asyncio
async def test_update_instance_blocks_run_startup_until_the_update_finishes(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")
    created.data.provider_id = "provider-a"
    created.data.history = [{"role": "user", "content": "persisted"}]
    created.data.token_usage = 9
    created.data.begin_dialogs_injected = True

    update_started = asyncio.Event()
    allow_update_to_finish = asyncio.Event()
    run_started = asyncio.Event()
    allow_run_to_finish = asyncio.Event()

    async def fake_execute(event_arg, instance, messages, input_text, image_urls):
        assert event_arg is event
        assert instance.instance_id == created.data.instance_id
        assert input_text == "hello"
        assert image_urls is None
        run_started.set()
        await allow_run_to_finish.wait()
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 10,
        }

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)

    original_update = runtime.db.update_subagent_instance

    async def blocked_update(instance_id, **kwargs):
        update_started.set()
        await allow_update_to_finish.wait()
        return await original_update(instance_id, **kwargs)

    monkeypatch.setattr(runtime.db, "update_subagent_instance", blocked_update)
    update_task = asyncio.create_task(
        runtime.update_instance(
            event,
            "agent",
            updates={"provider_id": "provider-b"},
        )
    )

    run_task = None
    try:
        await asyncio.wait_for(update_started.wait(), timeout=1)

        run_task = asyncio.create_task(runtime.run_instance(event, "agent", "hello"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert run_started.is_set() is False
        assert runtime.is_instance_locked(created.data.instance_id) is False
        assert runtime.db.updated_instances == []
        assert runtime.db.instances[0].provider_id == "provider-a"

        allow_update_to_finish.set()
        update_result = await asyncio.wait_for(update_task, timeout=1)

        assert update_result.ok is True
        assert update_result.data.provider_id == "provider-b"
        assert runtime.db.updated_instances == [
            (created.data.instance_id, {"provider_id": "provider-b"})
        ]

        await asyncio.wait_for(run_started.wait(), timeout=1)
        assert runtime.is_instance_locked(created.data.instance_id) is True
        allow_run_to_finish.set()
        run_result = await asyncio.wait_for(run_task, timeout=1)
        assert run_result.ok is True
    finally:
        allow_update_to_finish.set()
        allow_run_to_finish.set()
        if run_task is not None and not run_task.done():
            await asyncio.wait_for(run_task, timeout=1)
        if not update_task.done():
            await asyncio.wait_for(update_task, timeout=1)


@pytest.mark.asyncio
async def test_update_instance_returns_not_found_when_db_update_returns_none():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    await runtime.create_instance(event, "agent", "researcher")
    runtime.db.update_returns_none = True

    updated = await runtime.update_instance(
        event, "agent", updates={"provider_id": "provider-b"}
    )

    assert updated.ok is False
    assert updated.error.error_code == INSTANCE_NOT_FOUND


@pytest.mark.asyncio
async def test_reset_instance_clears_history_with_optimistic_versioning():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")
    created.data.history = [{"role": "user", "content": "hello"}]
    created.data.token_usage = 10
    created.data.begin_dialogs_injected = True

    reset = await runtime.reset_instance(event, "agent")

    assert reset.ok is True
    assert reset.data.history == []
    assert reset.data.token_usage == 0
    assert reset.data.begin_dialogs_injected is False
    assert reset.data.version == 2


@pytest.mark.asyncio
async def test_reset_instance_returns_version_conflict_when_save_fails():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    await runtime.create_instance(event, "agent", "researcher")
    runtime.db.save_returns_none = True

    reset = await runtime.reset_instance(event, "agent")

    assert reset.ok is False
    assert reset.error.error_code == VERSION_CONFLICT


@pytest.mark.asyncio
async def test_delete_instance_removes_instance_and_returns_deleted_data():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    deleted = await runtime.delete_instance(event, "agent")

    assert deleted.ok is True
    assert deleted.data == created.data
    assert runtime.db.deleted_instances == [created.data.instance_id]
    assert runtime.db.instances == []


@pytest.mark.asyncio
async def test_delete_instance_by_id_removes_instance_without_event_scope():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    deleted = await runtime.delete_instance_by_id(created.data.instance_id)

    assert deleted.ok is True
    assert deleted.data == {"instance_id": created.data.instance_id}
    assert runtime.db.deleted_instances == [created.data.instance_id]
    assert runtime.db.instances == []


@pytest.mark.asyncio
async def test_update_reset_and_delete_propagate_lookup_errors():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    await runtime.create_instance(event, "agent", "researcher")
    await runtime.create_instance(event, "agent", "researcher", scope_type="session")

    missing_update = await runtime.update_instance(
        event, "missing", updates={"provider_id": "provider-b"}
    )
    missing_reset = await runtime.reset_instance(event, "missing")
    missing_delete = await runtime.delete_instance(event, "missing")
    ambiguous_update = await runtime.update_instance(
        event, "agent", updates={"provider_id": "provider-b"}
    )
    ambiguous_reset = await runtime.reset_instance(event, "agent")
    ambiguous_delete = await runtime.delete_instance(event, "agent")

    assert missing_update.error.error_code == INSTANCE_NOT_FOUND
    assert missing_reset.error.error_code == INSTANCE_NOT_FOUND
    assert missing_delete.error.error_code == INSTANCE_NOT_FOUND
    assert ambiguous_update.error.error_code == AMBIGUOUS_INSTANCE
    assert ambiguous_reset.error.error_code == AMBIGUOUS_INSTANCE
    assert ambiguous_delete.error.error_code == AMBIGUOUS_INSTANCE


def test_prune_history_keeps_system_and_recent_turns():
    runtime = manager()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old assistant"},
        {"role": "user", "content": "recent user"},
        {"role": "assistant", "content": "recent assistant"},
    ]

    pruned = runtime.prune_history_for_persistence(messages, max_persisted_turns=1)

    assert pruned == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "recent user"},
        {"role": "assistant", "content": "recent assistant"},
    ]


def test_prune_history_zero_turns_keeps_only_leading_system_messages():
    runtime = manager()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old assistant"},
    ]

    pruned = runtime.prune_history_for_persistence(messages, max_persisted_turns=0)

    assert pruned == [{"role": "system", "content": "system"}]


def test_prune_history_applies_token_limit_after_turn_pruning():
    runtime = manager()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old " * 200},
        {"role": "assistant", "content": "old answer " * 200},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]

    pruned = runtime.prune_history_for_persistence(
        messages,
        max_persisted_turns=None,
        max_persisted_tokens=30,
    )

    assert pruned == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]


@pytest.mark.asyncio
async def test_save_history_returns_version_conflict_on_stale_save():
    runtime = manager()
    runtime.db.save_returns_none = True
    instance = SimpleNamespace(
        instance_id="instance-1",
        version=2,
        max_persisted_turns=20,
        max_persisted_tokens=None,
    )

    result = await runtime.save_history(
        instance,
        [{"role": "user", "content": "hello"}],
        token_usage=1,
        begin_dialogs_injected=True,
    )

    assert result.ok is False
    assert result.error.error_code == VERSION_CONFLICT


@pytest.mark.asyncio
async def test_instance_lock_reports_busy():
    runtime = manager()

    first = runtime.try_acquire_instance_lock("instance-1")
    async with first:
        assert runtime.is_instance_locked("instance-1") is True
        second = runtime.try_acquire_instance_lock("instance-1")
        assert second.ok is False
        assert second.error.error_code == "instance_busy"

    assert runtime.is_instance_locked("instance-1") is False


@pytest.mark.asyncio
async def test_run_instance_injects_begin_dialogs_once_and_saves_history():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {
                "agents": [
                    {
                        "name": "researcher",
                        "runtime_mode": "persistent",
                        "begin_dialogs": [{"role": "assistant", "content": "ready"}],
                    }
                ]
            }
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")
    calls = []

    async def fake_execute(event_arg, instance, messages, input_text, image_urls):
        calls.append(
            {
                "event": event_arg,
                "instance": instance,
                "messages": list(messages),
                "input": input_text,
                "image_urls": image_urls,
            }
        )
        return {
            "final_response": f"answer to {input_text}",
            "history": [*messages, {"role": "assistant", "content": "answer"}],
            "token_usage": 7,
        }

    runtime._execute_instance = fake_execute

    first = await runtime.run_instance(
        event,
        "agent",
        "question one",
        image_urls=["https://example.com/a.png"],
    )
    second = await runtime.run_instance(event, "agent", "question two")

    assert first.ok is True
    assert first.data["final_response"] == "answer to question one"
    assert first.data["metadata"] == {
        "instance_id": created.data.instance_id,
        "name": "agent",
        "preset_name": "researcher",
        "scope_type": "conversation",
        "scope_id": "conv-1",
        "version": 2,
        "token_usage": 7,
    }
    assert calls[0]["messages"] == [
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "question one"},
    ]
    assert calls[0]["image_urls"] == ["https://example.com/a.png"]
    assert second.ok is True
    assert calls[1]["messages"] == [
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "question one"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "question two"},
    ]
    assert runtime.db.instances[0].begin_dialogs_injected is True
    assert runtime.db.instances[0].token_usage == 7


@pytest.mark.asyncio
async def test_run_instance_propagates_lookup_errors_and_busy_lock():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    missing = await runtime.run_instance(event, "missing", "hello")
    runtime.try_acquire_instance_lock(created.data.instance_id)
    busy = await runtime.run_instance(event, "agent", "hello")

    assert missing.ok is False
    assert missing.error.error_code == INSTANCE_NOT_FOUND
    assert busy.ok is False
    assert busy.error.error_code == INSTANCE_BUSY


@pytest.mark.asyncio
async def test_run_instance_returns_not_found_when_deleted_before_lifecycle_lock(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    run_waiting = asyncio.Event()
    allow_run_to_enter = asyncio.Event()
    executed = asyncio.Event()
    original_lifecycle_lock = runtime._instance_lifecycle_lock
    intercepted = {"used": False}

    class DelayedLifecycleLock:
        def __init__(self, lock):
            self._lock = lock
            self._entered = False

        async def __aenter__(self):
            run_waiting.set()
            await allow_run_to_enter.wait()
            await self._lock.acquire()
            self._entered = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if self._entered:
                self._lock.release()

    def delayed_lifecycle_lock(instance_id):
        lock = original_lifecycle_lock(instance_id)
        if instance_id == created.data.instance_id and not intercepted["used"]:
            intercepted["used"] = True
            return DelayedLifecycleLock(lock)
        return lock

    async def fake_execute(event_arg, instance, messages, input_text, image_urls):
        executed.set()
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 2,
        }

    monkeypatch.setattr(runtime, "_instance_lifecycle_lock", delayed_lifecycle_lock)
    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)

    run_task = asyncio.create_task(runtime.run_instance(event, "agent", "hello"))
    await asyncio.wait_for(run_waiting.wait(), timeout=1)

    deleted = await runtime.delete_instance(event, "agent")
    assert deleted.ok is True

    allow_run_to_enter.set()
    result = await asyncio.wait_for(run_task, timeout=1)

    assert result.ok is False
    assert result.error.error_code == INSTANCE_NOT_FOUND
    assert executed.is_set() is False


@pytest.mark.asyncio
async def test_run_instance_returns_version_conflict_when_history_save_is_stale():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    await runtime.create_instance(event, "agent", "researcher")

    async def fake_execute(event_arg, instance, messages, input_text, image_urls):
        return {
            "final_response": "answer",
            "history": [*messages, {"role": "assistant", "content": "answer"}],
            "token_usage": 2,
        }

    runtime._execute_instance = fake_execute
    runtime.db.save_returns_none = True

    result = await runtime.run_instance(event, "agent", "hello")

    assert result.ok is False
    assert result.error.error_code == VERSION_CONFLICT


@pytest.mark.asyncio
async def test_run_instance_catches_execution_error_and_releases_lock():
    runtime = manager()
    runtime.reload_from_config(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    async def fake_execute(event_arg, instance, messages, input_text, image_urls):
        raise RuntimeError("provider unavailable with secret stack")

    runtime._execute_instance = fake_execute

    result = await runtime.run_instance(event, "agent", "hello")

    assert result.ok is False
    assert result.error.error_code == "subagent_execution_failed"
    assert result.error.message == "Sub-agent execution failed."
    assert result.error.details is None
    assert runtime.is_instance_locked(created.data.instance_id) is False


@pytest.mark.asyncio
async def test_run_instance_background_submits_task_reports_status_and_completes(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")

    started = asyncio.Event()
    release = asyncio.Event()
    wake_calls = []

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        assert event_arg is event
        assert instance.instance_id == created.data.instance_id
        assert input_text == "summarize this"
        assert image_urls == ["https://example.com/a.png"]
        assert agent_hooks is not None
        await agent_hooks.on_tool_start(
            None,
            SimpleNamespace(name="web_search"),
            {"query": "latest"},
        )
        started.set()
        await release.wait()
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    async def fake_wake(run_context, **kwargs):
        wake_calls.append({"run_context": run_context, **kwargs})

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        image_urls=["https://example.com/a.png"],
        background_task=True,
        tool_call_timeout=77,
    )

    assert submitted.ok is True
    assert submitted.data["background_task"] is True
    assert submitted.data["task_id"] == "task-1"
    assert submitted.data["status"] == "queued"
    assert submitted.data["metadata"] == {
        "instance_id": created.data.instance_id,
        "name": "analyst",
        "preset_name": "researcher",
        "scope_type": "conversation",
        "scope_id": "conv-1",
        "version": 1,
        "token_usage": 0,
    }

    second = await runtime.run_instance(
        event,
        "analyst",
        "run again",
        background_task=True,
        tool_call_timeout=77,
    )
    assert second.ok is False
    assert second.error.error_code == INSTANCE_BUSY

    await asyncio.wait_for(started.wait(), timeout=1)
    status = await runtime.get_instance_status(event, "analyst")

    assert status.ok is True
    assert status.data["busy"] is True
    assert status.data["background_run"]["task_id"] == submitted.data["task_id"]
    assert status.data["background_run"]["status"] == "running"
    assert any(
        evt["type"] == "tool_call" and evt["tool_name"] == "web_search"
        for evt in status.data["background_run"]["events"]
    )

    task = runtime._background_tasks[submitted.data["task_id"]]
    release.set()
    await asyncio.wait_for(task, timeout=1)

    completed = await runtime.get_instance_status(event, "analyst")

    assert completed.ok is True
    assert completed.data["busy"] is False
    assert completed.data["background_run"]["status"] == "completed"
    assert completed.data["background_run"]["final_response"] == "done"
    assert completed.data["background_run"]["error_message"] is None
    assert completed.data["background_run"]["completed_at"] is not None
    assert completed.data["background_run"]["events"][-1]["type"] == "completed"
    assert runtime.db.instances[0].history[-1] == {
        "role": "assistant",
        "content": "done",
    }
    assert runtime.db.instances[0].token_usage == 3
    assert wake_calls[0]["task_id"] == submitted.data["task_id"]
    assert wake_calls[0]["result_text"] == "done"


@pytest.mark.asyncio
async def test_completed_status_update_failure_does_not_downgrade_saved_run(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    await runtime.create_instance(event, "analyst", "researcher")
    wake_calls = []
    original_update = runtime.db.update_subagent_background_run

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    async def flaky_update(task_id, **kwargs):
        if kwargs.get("status") == "completed":
            raise RuntimeError("persist completed status failed")
        return await original_update(task_id, **kwargs)

    async def fake_wake(run_context, **kwargs):
        wake_calls.append({"run_context": run_context, **kwargs})

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(runtime.db, "update_subagent_background_run", flaky_update)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
        tool_call_timeout=77,
    )
    assert submitted.ok is True

    task = runtime._background_tasks[submitted.data["task_id"]]
    await asyncio.wait_for(task, timeout=1)

    status = await runtime.get_instance_status(event, "analyst")
    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["status"] != "failed"
    assert runtime.db.instances[0].history[-1] == {
        "role": "assistant",
        "content": "done",
    }
    assert wake_calls[0]["result_text"] == "done"


@pytest.mark.asyncio
async def test_cancellation_during_completed_status_persistence_preserves_success_state(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    await runtime.create_instance(event, "analyst", "researcher")
    wake_calls = []
    original_update = runtime.db.update_subagent_background_run

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    async def flaky_update(task_id, **kwargs):
        if kwargs.get("status") == "completed":
            raise asyncio.CancelledError()
        return await original_update(task_id, **kwargs)

    async def fake_wake(run_context, **kwargs):
        wake_calls.append({"run_context": run_context, **kwargs})

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(runtime.db, "update_subagent_background_run", flaky_update)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
        tool_call_timeout=77,
    )
    assert submitted.ok is True

    task = runtime._background_tasks[submitted.data["task_id"]]
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    status = await runtime.get_instance_status(event, "analyst")
    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["status"] == "completed"
    assert status.data["background_run"]["final_response"] == "done"
    assert wake_calls[0]["result_text"] == "done"


@pytest.mark.asyncio
async def test_restarted_manager_reconciles_completed_run_after_completed_status_write_failed(
    monkeypatch: pytest.MonkeyPatch,
):
    db = FakeDB()
    config = enabled_runtime_config(
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
    )
    runtime = manager_with_db(db, config)
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    await runtime.create_instance(event, "analyst", "researcher")
    original_update = db.update_subagent_background_run

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    async def flaky_update(task_id, **kwargs):
        if kwargs.get("status") == "completed":
            raise RuntimeError("persist completed status failed")
        return await original_update(task_id, **kwargs)

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(db, "update_subagent_background_run", flaky_update)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        lambda run_context, **kwargs: asyncio.sleep(0),
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
        tool_call_timeout=77,
    )
    assert submitted.ok is True

    task = runtime._background_tasks[submitted.data["task_id"]]
    await asyncio.wait_for(task, timeout=1)

    restarted = manager_with_db(db, config)
    status = await restarted.get_instance_status(event, "analyst")

    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["status"] == "completed"
    assert status.data["background_run"]["final_response"] == "done"
    assert db.instances[0].history[-1] == {"role": "assistant", "content": "done"}


@pytest.mark.asyncio
async def test_run_instance_background_marks_failed_run(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")

    started = asyncio.Event()
    release = asyncio.Event()
    wake_calls = []

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        assert event_arg is event
        assert instance.instance_id == created.data.instance_id
        assert input_text == "summarize this"
        assert image_urls == []
        assert agent_hooks is not None
        await agent_hooks.on_tool_start(
            None,
            SimpleNamespace(name="web_search"),
            {"query": "latest"},
        )
        started.set()
        await release.wait()
        await agent_hooks.on_tool_end(
            None,
            SimpleNamespace(name="web_search"),
            {"query": "latest"},
            SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(text="tool exploded")],
            ),
        )
        raise RuntimeError("provider unavailable")

    async def fake_wake(run_context, **kwargs):
        wake_calls.append({"run_context": run_context, **kwargs})

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
        tool_call_timeout=77,
    )

    assert submitted.ok is True
    assert submitted.data["background_task"] is True
    assert submitted.data["status"] == "queued"

    await asyncio.wait_for(started.wait(), timeout=1)
    task = runtime._background_tasks[submitted.data["task_id"]]
    release.set()
    await asyncio.wait_for(task, timeout=1)

    status = await runtime.get_instance_status(event, "analyst")

    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["task_id"] == submitted.data["task_id"]
    assert status.data["background_run"]["status"] == "failed"
    assert status.data["background_run"]["final_response"] is None
    assert status.data["background_run"]["error_message"] == "provider unavailable"
    assert status.data["background_run"]["completed_at"] is not None
    assert any(
        evt["type"] == "tool_error" and evt["tool_name"] == "web_search"
        for evt in status.data["background_run"]["events"]
    )
    assert status.data["background_run"]["events"][-1]["type"] == "failed"
    assert runtime.db.instances[0].history == []
    assert wake_calls[0]["task_id"] == submitted.data["task_id"]
    assert wake_calls[0]["result_text"] == "provider unavailable"


@pytest.mark.asyncio
async def test_tool_call_event_append_failure_does_not_fail_background_run(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")

    original_append = runtime._append_background_run_event

    async def flaky_append(task_id, event_type, message, **details):
        if event_type == "tool_call":
            raise RuntimeError("append tool_call event failed")
        await original_append(task_id, event_type, message, **details)

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        assert event_arg is event
        assert instance.instance_id == created.data.instance_id
        assert input_text == "summarize this"
        assert image_urls == []
        assert agent_hooks is not None
        await agent_hooks.on_tool_start(
            None,
            SimpleNamespace(name="web_search"),
            {"query": "latest"},
        )
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    async def fake_wake(run_context, **kwargs):
        return None

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(runtime, "_append_background_run_event", flaky_append)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
    )

    assert submitted.ok is True
    task = runtime._background_tasks[submitted.data["task_id"]]
    await asyncio.wait_for(task, timeout=1)

    latest_run = await runtime.db.get_latest_subagent_background_run(
        created.data.instance_id
    )

    assert latest_run is not None
    assert latest_run.status == "completed"
    assert latest_run.final_response == "done"


@pytest.mark.asyncio
async def test_completed_run_stays_completed_when_completed_event_append_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")
    wake_calls = []

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        assert event_arg is event
        assert instance.instance_id == created.data.instance_id
        assert input_text == "summarize this"
        assert image_urls == []
        assert agent_hooks is not None
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    original_append = runtime._append_background_run_event

    async def flaky_append(task_id, event_type, message, **details):
        if event_type == "completed":
            raise RuntimeError("append completed event failed")
        await original_append(task_id, event_type, message, **details)

    async def fake_wake(run_context, **kwargs):
        wake_calls.append({"run_context": run_context, **kwargs})

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(runtime, "_append_background_run_event", flaky_append)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
        tool_call_timeout=77,
    )

    assert submitted.ok is True
    task = runtime._background_tasks[submitted.data["task_id"]]
    await asyncio.wait_for(task, timeout=1)

    status = await runtime.get_instance_status(event, "analyst")

    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["status"] == "completed"
    assert status.data["background_run"]["final_response"] == "done"
    assert wake_calls[0]["result_text"] == "done"


@pytest.mark.asyncio
async def test_cancellation_after_durable_completion_does_not_downgrade_run(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")

    original_append = runtime._append_background_run_event

    async def flaky_append(task_id, event_type, message, **details):
        if event_type == "completed":
            raise asyncio.CancelledError()
        await original_append(task_id, event_type, message, **details)

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        assert event_arg is event
        assert instance.instance_id == created.data.instance_id
        assert input_text == "summarize this"
        assert image_urls == []
        assert agent_hooks is not None
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    async def fake_wake(run_context, **kwargs):
        return None

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(runtime, "_append_background_run_event", flaky_append)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
    )

    assert submitted.ok is True
    task = runtime._background_tasks[submitted.data["task_id"]]
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    latest_run = await runtime.db.get_latest_subagent_background_run(
        created.data.instance_id
    )

    assert latest_run is not None
    assert latest_run.status == "completed"
    assert latest_run.final_response == "done"


@pytest.mark.asyncio
async def test_cancellation_during_safe_event_append_propagates_and_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    await runtime.create_instance(event, "analyst", "researcher")
    started = asyncio.Event()

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        started.set()
        await agent_hooks.on_tool_start(
            None,
            SimpleNamespace(name="web_search"),
            {"query": "latest"},
        )
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    original_append = runtime._append_background_run_event

    async def cancelling_append(task_id, event_type, message, **details):
        if event_type == "tool_call":
            raise asyncio.CancelledError()
        await original_append(task_id, event_type, message, **details)

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(runtime, "_append_background_run_event", cancelling_append)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        lambda run_context, **kwargs: asyncio.sleep(0),
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
        tool_call_timeout=77,
    )
    assert submitted.ok is True

    await asyncio.wait_for(started.wait(), timeout=1)
    task = runtime._background_tasks[submitted.data["task_id"]]
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    status = await runtime.get_instance_status(event, "analyst")
    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["status"] == "failed"
    assert "cancelled" in status.data["background_run"]["error_message"].lower()


@pytest.mark.asyncio
async def test_run_instance_releases_lock_when_setup_raises_before_execution():
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    def raising_get_extra(key):
        raise RuntimeError("setup failed before execution")

    event.get_extra = raising_get_extra

    result = await runtime.run_instance(
        event,
        "agent",
        "hello",
        background_task=True,
    )

    assert result.ok is False
    assert result.error.error_code == "subagent_execution_failed"
    assert runtime.is_instance_locked(created.data.instance_id) is False


@pytest.mark.asyncio
async def test_run_instance_background_submission_failure_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")

    def fail_create_task(coro):
        coro.close()
        raise RuntimeError("scheduler failed")

    monkeypatch.setattr(
        "astrbot.core.subagent_runtime.asyncio.create_task",
        fail_create_task,
    )

    result = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
    )

    assert result.ok is False
    assert result.error.error_code == "subagent_execution_failed"
    assert runtime.is_instance_locked(created.data.instance_id) is False

    latest_run = await runtime.db.get_latest_subagent_background_run(
        created.data.instance_id
    )

    assert latest_run is not None
    assert latest_run.status == "failed"
    assert latest_run.error_message == "scheduler failed"
    assert latest_run.completed_at is not None
    assert latest_run.events[-1]["type"] == "failed"


@pytest.mark.asyncio
async def test_run_instance_background_submission_failure_releases_lock_when_failed_event_append_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")

    async def fail_failed_event(task_id, event_type, message, **details):
        raise RuntimeError("append failed event failed")

    def fail_create_task(coro):
        coro.close()
        raise RuntimeError("scheduler failed")

    monkeypatch.setattr(
        "astrbot.core.subagent_runtime.asyncio.create_task",
        fail_create_task,
    )
    monkeypatch.setattr(runtime, "_append_background_run_event", fail_failed_event)

    result = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
    )

    assert result.ok is False
    assert result.error.error_code == "subagent_execution_failed"
    assert runtime.is_instance_locked(created.data.instance_id) is False

    latest_run = await runtime.db.get_latest_subagent_background_run(
        created.data.instance_id
    )

    assert latest_run is not None
    assert latest_run.status == "failed"
    assert latest_run.error_message == "scheduler failed"


@pytest.mark.asyncio
async def test_run_instance_background_cancellation_marks_failed_run(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    created = await runtime.create_instance(event, "analyst", "researcher")

    started = asyncio.Event()
    blocked = asyncio.Event()
    wake_calls = []

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        assert event_arg is event
        assert instance.instance_id == created.data.instance_id
        assert input_text == "summarize this"
        assert image_urls == []
        assert agent_hooks is not None
        started.set()
        await blocked.wait()

    async def fake_wake(run_context, **kwargs):
        wake_calls.append({"run_context": run_context, **kwargs})

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
    )

    assert submitted.ok is True
    await asyncio.wait_for(started.wait(), timeout=1)

    latest_run = await runtime.db.get_latest_subagent_background_run(
        created.data.instance_id
    )
    assert latest_run is not None
    assert latest_run.status == "running"

    task = runtime._background_tasks[submitted.data["task_id"]]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    status = await runtime.get_instance_status(event, "analyst")

    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["status"] == "failed"
    assert "cancel" in status.data["background_run"]["error_message"].lower()
    assert status.data["background_run"]["events"][-1]["type"] == "failed"
    assert runtime.is_instance_locked(created.data.instance_id) is False
    assert "cancel" in wake_calls[0]["result_text"].lower()


@pytest.mark.asyncio
async def test_failure_status_persistence_error_still_wakes_main_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    await runtime.create_instance(event, "analyst", "researcher")
    wake_calls = []
    original_update = runtime.db.update_subagent_background_run

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        raise RuntimeError("provider unavailable")

    async def flaky_update(task_id, **kwargs):
        if kwargs.get("status") == "failed":
            raise RuntimeError("persist failed status failed")
        return await original_update(task_id, **kwargs)

    async def fake_wake(run_context, **kwargs):
        wake_calls.append({"run_context": run_context, **kwargs})

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(runtime.db, "update_subagent_background_run", flaky_update)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
        tool_call_timeout=77,
    )
    assert submitted.ok is True

    task = runtime._background_tasks[submitted.data["task_id"]]
    await asyncio.wait_for(task, timeout=1)

    assert wake_calls[0]["result_text"] == "provider unavailable"


@pytest.mark.asyncio
async def test_run_instance_background_removes_finished_task_from_active_tracking(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    plugin_context = SimpleNamespace()
    event.get_extra = lambda key: (
        plugin_context if key == "subagent_runtime_context" else None
    )
    await runtime.create_instance(event, "analyst", "researcher")

    async def fake_execute(
        event_arg,
        instance,
        messages,
        input_text,
        image_urls,
        *,
        agent_hooks=None,
    ):
        assert event_arg is event
        assert agent_hooks is not None
        return {
            "final_response": "done",
            "history": [*messages, {"role": "assistant", "content": "done"}],
            "token_usage": 3,
        }

    async def fake_wake(run_context, **kwargs):
        return None

    monkeypatch.setattr(runtime, "_execute_instance", fake_execute)
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        fake_wake,
        raising=False,
    )

    submitted = await runtime.run_instance(
        event,
        "analyst",
        "summarize this",
        background_task=True,
    )

    task_id = submitted.data["task_id"]
    task = runtime._background_tasks[task_id]
    await asyncio.wait_for(task, timeout=1)

    assert task_id not in runtime._background_tasks


@pytest.mark.asyncio
async def test_get_instance_status_reconciles_stale_background_run_state():
    runtime = manager(
        enabled_runtime_config(
            {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
        )
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "analyst", "researcher")

    stale_run = await runtime.db.create_subagent_background_run(
        instance_id=created.data.instance_id,
        umo=created.data.umo,
        scope_type=created.data.scope_type,
        scope_id=created.data.scope_id,
        instance_name=created.data.name,
        preset_name=created.data.preset_name,
        status="running",
        input_text="summarize this",
        image_urls=[],
        events=[
            runtime._background_event("queued", "Background run queued."),
            runtime._background_event("started", "Background run started."),
        ],
    )

    assert stale_run.task_id not in runtime._background_tasks

    status = await runtime.get_instance_status(event, "analyst")

    assert status.ok is True
    assert status.data["busy"] is False
    assert status.data["background_run"]["task_id"] == stale_run.task_id
    assert status.data["background_run"]["status"] == "failed"
    assert "stale" in status.data["background_run"]["error_message"].lower()
    assert status.data["background_run"]["events"][-1]["type"] == "failed"


@pytest.mark.asyncio
async def test_get_instance_status_retries_durable_completion_when_override_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    db = FakeDB()
    config = enabled_runtime_config(
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
    )
    runtime = manager_with_db(db, config)
    event = FakeEvent()
    created = await runtime.create_instance(event, "analyst", "researcher")
    run = await db.create_subagent_background_run(
        instance_id=created.data.instance_id,
        umo=created.data.umo,
        scope_type=created.data.scope_type,
        scope_id=created.data.scope_id,
        instance_name=created.data.name,
        preset_name=created.data.preset_name,
        status="running",
        input_text="summarize this",
        image_urls=[],
        events=[],
    )
    run.final_response = "done"
    run.token_usage = 3
    run.completed_at = datetime.now(timezone.utc)
    runtime._background_run_terminal_overrides[run.task_id] = {
        "status": "completed",
        "final_response": "done",
        "error_message": None,
        "token_usage": 3,
        "completed_at": run.completed_at,
        "updated_at": run.completed_at,
    }
    persisted = []
    original_update = db.update_subagent_background_run

    async def tracking_update(task_id, **kwargs):
        persisted.append(kwargs)
        return await original_update(task_id, **kwargs)

    monkeypatch.setattr(db, "update_subagent_background_run", tracking_update)

    status = await runtime.get_instance_status(event, "analyst")

    assert status.ok is True
    assert status.data["background_run"]["status"] == "completed"
    assert any(kwargs.get("status") == "completed" for kwargs in persisted)

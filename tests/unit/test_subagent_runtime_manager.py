from types import SimpleNamespace

import pytest

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
)


class FakeDB:
    def __init__(self):
        self.instances = []
        self.next_id = 1
        self.save_returns_none = False
        self.update_returns_none = False
        self.updated_instances = []
        self.deleted_instances = []
        self.deleted_sessions = []
        self.deleted_conversations = []

    async def create_subagent_instance(self, **kwargs):
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


@pytest.mark.asyncio
async def test_cleanup_for_session_deletes_persisted_session_instances():
    runtime = manager()

    await runtime.cleanup_for_session("telegram:FriendMessage:user1")

    assert runtime.db.deleted_sessions == ["telegram:FriendMessage:user1"]


@pytest.mark.asyncio
async def test_cleanup_for_conversation_deletes_persisted_conversation_instances():
    runtime = manager()

    await runtime.cleanup_for_conversation("conversation-1")

    assert runtime.db.deleted_conversations == ["conversation-1"]


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

    assert [preset.name for preset in presets] == ["writer"]
    assert presets[0].instructions == "Write carefully."
    assert presets[0].tools == ["tool_a"]
    assert presets[0].skills == ["skill_a"]


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

    assert len(presets) == 1
    assert presets[0].name == "legacy"
    assert presets[0].runtime_mode == "handoff"


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
        {
            "max_instances_per_scope": 1,
            "agents": [{"name": "researcher", "runtime_mode": "persistent"}],
        }
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
        {
            "agents": [
                {"name": "legacy", "runtime_mode": "handoff"},
            ]
        }
    )
    event = FakeEvent()

    missing = await runtime.create_instance(event, "agent", "missing")
    handoff = await runtime.create_instance(event, "agent", "legacy")

    assert missing.ok is False
    assert missing.error.error_code == PRESET_NOT_FOUND
    assert handoff.ok is False
    assert handoff.error.error_code == PRESET_NOT_FOUND


@pytest.mark.asyncio
async def test_create_instance_validates_tool_and_skill_overrides():
    runtime = manager(tools=["web_search"], skills=["summarize"])
    runtime.reload_from_config(
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
async def test_get_instance_reports_ambiguous_name_across_scopes():
    runtime = manager()
    runtime.reload_from_config(
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
async def test_update_instance_returns_not_found_when_db_update_returns_none():
    runtime = manager()
    runtime.reload_from_config(
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
    )
    event = FakeEvent()
    created = await runtime.create_instance(event, "agent", "researcher")

    deleted = await runtime.delete_instance(event, "agent")

    assert deleted.ok is True
    assert deleted.data == created.data
    assert runtime.db.deleted_instances == [created.data.instance_id]
    assert runtime.db.instances == []


@pytest.mark.asyncio
async def test_update_reset_and_delete_propagate_lookup_errors():
    runtime = manager()
    runtime.reload_from_config(
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
async def test_run_instance_returns_version_conflict_when_history_save_is_stale():
    runtime = manager()
    runtime.reload_from_config(
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
        {"agents": [{"name": "researcher", "runtime_mode": "persistent"}]}
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
    assert result.error.details == {"error": "provider unavailable with secret stack"}
    assert runtime.is_instance_locked(created.data.instance_id) is False

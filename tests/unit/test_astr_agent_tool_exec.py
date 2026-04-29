from types import SimpleNamespace

import mcp
import pytest

from astrbot.core import astr_agent_tool_exec as tool_exec
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_tool_exec import (
    FunctionToolExecutor,
    execute_persistent_subagent,
)
from astrbot.core.message.components import Image
from astrbot.core.provider.entities import LLMResponse, TokenUsage
from astrbot.core.tools.skill_tool import SkillTool


class _DummyEvent:
    def __init__(self, message_components: list[object] | None = None) -> None:
        self.unified_msg_origin = "webchat:FriendMessage:webchat!user!session"
        self.message_obj = SimpleNamespace(message=message_components or [])

    def get_extra(self, _key: str):
        return None


class _DummyTool:
    def __init__(self) -> None:
        self.name = "transfer_to_subagent"
        self.agent = SimpleNamespace(name="subagent")


def _build_run_context(message_components: list[object] | None = None):
    event = _DummyEvent(message_components=message_components)
    ctx = SimpleNamespace(event=event, context=SimpleNamespace())
    return ContextWrapper(context=ctx)


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_normalizes_filters_and_appends_event_image(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/event_image.png"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls_input = (
        " https://example.com/a.png ",
        "/tmp/not_an_image.txt",
        "/tmp/local.webp",
        123,
    )

    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        image_urls_input,
    )

    assert image_urls == [
        "https://example.com/a.png",
        "/tmp/local.webp",
        "/tmp/event_image.png",
    ]


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_skips_failed_event_image_conversion(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        ["https://example.com/a.png"],
    )

    assert image_urls == ["https://example.com/a.png"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_refs", "expected_supported_refs"),
    [
        pytest.param(
            (
                "https://example.com/valid.png",
                "base64://iVBORw0KGgoAAAANSUhEUgAAAAUA",
                "file:///tmp/photo.heic",
                "file://localhost/tmp/vector.svg",
                "file://fileserver/share/image.webp",
                "file:///tmp/not-image.txt",
                "mailto:user@example.com",
                "random-string-without-scheme-or-extension",
            ),
            {
                "https://example.com/valid.png",
                "base64://iVBORw0KGgoAAAANSUhEUgAAAAUA",
                "file:///tmp/photo.heic",
                "file://localhost/tmp/vector.svg",
                "file://fileserver/share/image.webp",
            },
            id="mixed_supported_and_unsupported_refs",
        ),
    ],
)
async def test_collect_handoff_image_urls_filters_supported_schemes_and_extensions(
    image_refs: tuple[str, ...],
    expected_supported_refs: set[str],
):
    run_context = _build_run_context([])
    result = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context, image_refs
    )
    assert set(result) == expected_supported_refs


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_collects_event_image_when_args_is_none(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/event_only.png"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        None,
    )

    assert image_urls == ["/tmp/event_only.png"]


@pytest.mark.asyncio
async def test_do_handoff_background_reports_prepared_image_urls(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def _fake_execute_handoff(
        cls, tool, run_context, image_urls_prepared=False, **tool_args
    ):
        assert image_urls_prepared is True
        yield mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="ok")]
        )

    async def _fake_wake(cls, run_context, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        FunctionToolExecutor,
        "_execute_handoff",
        classmethod(_fake_execute_handoff),
    )
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        classmethod(_fake_wake),
    )

    run_context = _build_run_context()
    await FunctionToolExecutor._do_handoff_background(
        tool=_DummyTool(),
        run_context=run_context,
        task_id="task-id",
        input="hello",
        image_urls="https://example.com/raw.png",
    )

    assert captured["tool_args"]["image_urls"] == ["https://example.com/raw.png"]


@pytest.mark.asyncio
async def test_execute_handoff_skips_renormalize_when_image_urls_prepared(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    def _boom(_items):
        raise RuntimeError("normalize should not be called")

    async def _fake_get_current_chat_provider_id(_umo):
        return "provider-id"

    async def _fake_tool_loop_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(completion_text="ok")

    context = SimpleNamespace(
        get_current_chat_provider_id=_fake_get_current_chat_provider_id,
        tool_loop_agent=_fake_tool_loop_agent,
        get_config=lambda **_kwargs: {"provider_settings": {}},
    )
    event = _DummyEvent([])
    run_context = ContextWrapper(context=SimpleNamespace(event=event, context=context))
    tool = SimpleNamespace(
        name="transfer_to_subagent",
        provider_id=None,
        agent=SimpleNamespace(
            name="subagent",
            tools=[],
            instructions="subagent-instructions",
            begin_dialogs=[],
            run_hooks=None,
        ),
    )

    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.normalize_and_dedupe_strings", _boom
    )

    results = []
    async for result in FunctionToolExecutor._execute_handoff(
        tool,
        run_context,
        image_urls_prepared=True,
        input="hello",
        image_urls=["https://example.com/raw.png"],
    ):
        results.append(result)

    assert len(results) == 1
    assert captured["image_urls"] == ["https://example.com/raw.png"]


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_keeps_extensionless_existing_event_file(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/astrbot-handoff-image"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.get_astrbot_temp_path", lambda: "/tmp"
    )
    monkeypatch.setattr(
        "astrbot.core.utils.image_ref_utils.os.path.exists", lambda _: True
    )

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        [],
    )

    assert image_urls == ["/tmp/astrbot-handoff-image"]


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_filters_extensionless_missing_event_file(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/astrbot-handoff-missing-image"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.get_astrbot_temp_path", lambda: "/tmp"
    )
    monkeypatch.setattr(
        "astrbot.core.utils.image_ref_utils.os.path.exists", lambda _: False
    )

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        [],
    )

    assert image_urls == []


@pytest.mark.asyncio
async def test_execute_handoff_passes_tool_call_timeout_to_tool_loop_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def _fake_get_current_chat_provider_id(_umo):
        return "provider-id"

    async def _fake_tool_loop_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(completion_text="ok")

    context = SimpleNamespace(
        get_current_chat_provider_id=_fake_get_current_chat_provider_id,
        tool_loop_agent=_fake_tool_loop_agent,
        get_config=lambda **_kwargs: {"provider_settings": {}},
    )
    event = _DummyEvent([])
    run_context = ContextWrapper(
        context=SimpleNamespace(event=event, context=context),
        tool_call_timeout=120,
    )
    tool = SimpleNamespace(
        name="transfer_to_subagent",
        provider_id=None,
        agent=SimpleNamespace(
            name="subagent",
            tools=[],
            instructions="subagent-instructions",
            begin_dialogs=[],
            run_hooks=None,
        ),
    )

    results = []
    async for result in FunctionToolExecutor._execute_handoff(
        tool,
        run_context,
        image_urls_prepared=True,
        input="hello",
        image_urls=[],
    ):
        results.append(result)

    assert len(results) == 1
    assert captured["tool_call_timeout"] == 120


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_filters_extensionless_file_outside_temp_root(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/var/tmp/astrbot-handoff-image"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.get_astrbot_temp_path", lambda: "/tmp"
    )
    monkeypatch.setattr(
        "astrbot.core.utils.image_ref_utils.os.path.exists", lambda _: True
    )

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        [],
    )

    assert image_urls == []


@pytest.mark.asyncio
async def test_execute_persistent_subagent_passes_sanitized_explicit_and_event_images(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def _fake_convert_to_file_path(self):
        return "/tmp/event_image.png"

    async def _fake_get_current_chat_provider_id(_umo):
        return "provider-id"

    async def _fake_tool_loop_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(completion_text="ok", token_usage=3)

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AstrAgentContext",
        lambda context, event: SimpleNamespace(context=context, event=event),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AgentContextWrapper",
        lambda context: ContextWrapper(context=context),
    )
    context = SimpleNamespace(
        get_current_chat_provider_id=_fake_get_current_chat_provider_id,
        tool_loop_agent=_fake_tool_loop_agent,
        get_config=lambda **_kwargs: {"provider_settings": {}},
    )
    event = _DummyEvent([Image(file="file:///tmp/original.png")])

    def _get_extra(key):
        if key == "subagent_runtime_context":
            return context
        return None

    event.get_extra = _get_extra
    instance = SimpleNamespace(
        tools=[],
        provider_id=None,
        system_prompt="system",
        system_prompt_delta=None,
        token_usage=0,
    )

    result = await execute_persistent_subagent(
        event,
        instance,
        [{"role": "user", "content": "hello"}],
        "hello",
        image_urls=[
            " https://example.com/a.png ",
            "https://example.com/a.png",
            "/tmp/not-image.txt",
        ],
    )

    assert result["final_response"] == "ok"
    assert captured["image_urls"] == [
        "https://example.com/a.png",
        "/tmp/event_image.png",
    ]


@pytest.mark.asyncio
async def test_execute_persistent_subagent_injects_selected_skills_prompt(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    class FakeSkillManager:
        def list_skills(self, *, active_only=True, runtime="local"):
            assert active_only is True
            assert runtime == "local"
            return [
                SimpleNamespace(name="summarize", description="Summarize text"),
                SimpleNamespace(name="draft", description="Draft text"),
            ]

    def fake_build_skills_prompt(skills):
        return "SKILLS: " + ",".join(skill.name for skill in skills)

    async def _fake_tool_loop_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(completion_text="ok", token_usage=3)

    async def _fake_get_current_chat_provider_id(_umo):
        return "provider-id"

    monkeypatch.setattr(tool_exec, "SkillManager", FakeSkillManager, raising=False)
    monkeypatch.setattr(
        tool_exec,
        "build_skills_prompt",
        fake_build_skills_prompt,
        raising=False,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AstrAgentContext",
        lambda context, event: SimpleNamespace(context=context, event=event),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AgentContextWrapper",
        lambda context: ContextWrapper(context=context),
    )
    context = SimpleNamespace(
        get_current_chat_provider_id=_fake_get_current_chat_provider_id,
        tool_loop_agent=_fake_tool_loop_agent,
        get_config=lambda **_kwargs: {
            "provider_settings": {"computer_use_runtime": "local"}
        },
    )
    event = _DummyEvent([])
    event.get_extra = lambda key: context if key == "subagent_runtime_context" else None
    instance = SimpleNamespace(
        tools=[],
        skills=["summarize"],
        provider_id=None,
        system_prompt="system",
        system_prompt_delta=None,
        token_usage=0,
    )

    await execute_persistent_subagent(
        event,
        instance,
        [{"role": "user", "content": "hello"}],
        "hello",
    )

    assert captured["system_prompt"] == "system\nSKILLS: summarize"


@pytest.mark.asyncio
async def test_execute_persistent_subagent_adds_skill_tool_for_selected_skills(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    class FakeSkillManager:
        def list_skills(self, *, active_only=True, runtime="local"):
            assert active_only is True
            assert runtime == "local"
            return [SimpleNamespace(name="summarize", description="Summarize text")]

    def fake_build_skills_prompt(skills):
        return "SKILLS: " + ",".join(skill.name for skill in skills)

    async def _fake_tool_loop_agent(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            role="assistant",
            completion_text="ok",
            usage=TokenUsage(input_other=2, input_cached=1, output=4),
        )

    async def _fake_get_current_chat_provider_id(_umo):
        return "provider-id"

    monkeypatch.setattr(tool_exec, "SkillManager", FakeSkillManager, raising=False)
    monkeypatch.setattr(
        tool_exec,
        "build_skills_prompt",
        fake_build_skills_prompt,
        raising=False,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AstrAgentContext",
        lambda context, event: SimpleNamespace(context=context, event=event),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AgentContextWrapper",
        lambda context: ContextWrapper(context=context),
    )
    context = SimpleNamespace(
        get_current_chat_provider_id=_fake_get_current_chat_provider_id,
        tool_loop_agent=_fake_tool_loop_agent,
        get_config=lambda **_kwargs: {
            "provider_settings": {"computer_use_runtime": "local"}
        },
    )
    event = _DummyEvent([])
    event.get_extra = lambda key: context if key == "subagent_runtime_context" else None
    instance = SimpleNamespace(
        tools=[],
        skills=["summarize"],
        provider_id=None,
        system_prompt="system",
        system_prompt_delta=None,
        token_usage=99,
    )

    result = await execute_persistent_subagent(
        event,
        instance,
        [{"role": "user", "content": "hello"}],
        "hello",
    )

    skill_tool = captured["tools"].get_tool("skill")
    assert isinstance(skill_tool, SkillTool)
    assert sorted(skill_tool.allowed_skills) == ["summarize"]
    assert skill_tool.runtime == "local"
    assert result["token_usage"] == 7


@pytest.mark.asyncio
async def test_execute_persistent_subagent_returns_none_token_usage_without_llm_usage(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_tool_loop_agent(**kwargs):
        return LLMResponse(role="assistant", completion_text="ok")

    async def _fake_get_current_chat_provider_id(_umo):
        return "provider-id"

    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AstrAgentContext",
        lambda context, event: SimpleNamespace(context=context, event=event),
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.AgentContextWrapper",
        lambda context: ContextWrapper(context=context),
    )
    context = SimpleNamespace(
        get_current_chat_provider_id=_fake_get_current_chat_provider_id,
        tool_loop_agent=_fake_tool_loop_agent,
        get_config=lambda **_kwargs: {"provider_settings": {}},
    )
    event = _DummyEvent([])
    event.get_extra = lambda key: context if key == "subagent_runtime_context" else None
    instance = SimpleNamespace(
        tools=[],
        skills=[],
        provider_id=None,
        system_prompt="system",
        system_prompt_delta=None,
        token_usage=99,
    )

    result = await execute_persistent_subagent(
        event,
        instance,
        [{"role": "user", "content": "hello"}],
        "hello",
    )

    assert result["token_usage"] is None

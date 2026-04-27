# Dedicated SkillTool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a request-scoped `skill` tool that loads skill instructions through the correct local or sandbox runtime and lets the runner track activated skills.

**Architecture:** `SkillManager` remains the source of skill discovery and gains lookup/listing helpers. `SkillTool` is a normal per-request `FunctionTool` instance, not a cached builtin, and `_ensure_persona_and_skills()` injects it after persona skill filtering. `ToolLoopAgentRunner` records successful `skill` tool calls and sends a transient system reminder on later provider requests.

**Tech Stack:** Python 3, pytest, pydantic dataclasses, MCP `CallToolResult`, existing AstrBot `FunctionTool` and `ToolSet` APIs.

---

## File Structure

- Modify `astrbot/core/skills/skill_manager.py`: add `get_skill()`, `list_local_skill_files()`, and update `build_skills_prompt()` rules to prefer the `skill` tool.
- Create `astrbot/core/tools/skill_tool.py`: implement request-scoped skill loading and auxiliary file listing.
- Modify `astrbot/core/astr_main_agent.py`: import `SkillTool` and inject it after persona toolset construction when filtered skills are available.
- Modify `astrbot/core/agent/runners/tool_loop_agent_runner.py`: add activation tracking and transient reminders.
- Modify `tests/test_skill_metadata_enrichment.py`: update prompt expectations and add manager helper tests.
- Create `tests/test_skill_tool.py`: cover local loading, whitelist errors, sandbox loading, and sandbox listing failures.
- Modify `tests/unit/test_astr_main_agent.py`: cover `SkillTool` injection with normal and explicit persona tool lists.
- Modify `tests/test_tool_loop_agent_runner.py`: cover activation tracking and reminder injection.

Do not commit during execution unless the user explicitly asks for a commit.

---

### Task 1: SkillManager Helpers And Prompt Rules

**Files:**
- Modify: `tests/test_skill_metadata_enrichment.py`
- Modify: `astrbot/core/skills/skill_manager.py`

- [ ] **Step 1: Write failing tests for skill lookup, auxiliary files, and prompt migration**

Add these tests to `tests/test_skill_metadata_enrichment.py`:

```python
def test_build_skills_prompt_instructs_skill_tool_instead_of_shell_read():
    skills = [
        SkillInfo(
            name="test",
            description="test skill",
            path="/skills/test/SKILL.md",
            active=True,
        )
    ]
    prompt = build_skills_prompt(skills)

    assert "call the `skill` tool" in prompt
    assert "Mandatory grounding" not in prompt
    assert "first read its `SKILL.md` by running a shell command" not in prompt


def test_get_skill_respects_runtime_and_active_status(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    temp_dir = tmp_path / "temp"
    skills_root = tmp_path / "skills"
    data_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    skills_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_data_path",
        lambda: str(data_dir),
    )
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_temp_path",
        lambda: str(temp_dir),
    )
    skill_dir = skills_root / "local-skill"
    skill_dir.mkdir()
    skill_dir.joinpath("SKILL.md").write_text("# Local\n", encoding="utf-8")

    mgr = SkillManager(skills_root=str(skills_root))
    assert mgr.get_skill("local-skill", runtime="local") is not None
    assert mgr.get_skill("missing", runtime="local") is None

    mgr.set_skill_active("local-skill", False)
    assert mgr.get_skill("local-skill", runtime="local") is None


def test_list_local_skill_files_returns_sorted_relative_auxiliary_files(
    monkeypatch,
    tmp_path: Path,
):
    data_dir = tmp_path / "data"
    temp_dir = tmp_path / "temp"
    skills_root = tmp_path / "skills"
    data_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    skill_dir = skills_root / "local-skill"
    nested_dir = skill_dir / "scripts"
    nested_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_data_path",
        lambda: str(data_dir),
    )
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.get_astrbot_temp_path",
        lambda: str(temp_dir),
    )
    skill_dir.joinpath("SKILL.md").write_text("# Local\n", encoding="utf-8")
    skill_dir.joinpath("README.md").write_text("readme", encoding="utf-8")
    nested_dir.joinpath("run.py").write_text("print('ok')", encoding="utf-8")

    mgr = SkillManager(skills_root=str(skills_root))
    assert mgr.list_local_skill_files("local-skill") == [
        "README.md",
        "scripts/run.py",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skill_metadata_enrichment.py -q`

Expected: fails because `get_skill()` and `list_local_skill_files()` do not exist, and prompt still contains mandatory shell-read rules.

- [ ] **Step 3: Implement minimal manager and prompt changes**

Add `get_skill()` and `list_local_skill_files()` to `SkillManager`. Update `build_skills_prompt()` rule 3 to say the model must call the `skill` tool before using a skill and may only fall back to filesystem reads if the `skill` tool is unavailable or errors.

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/test_skill_metadata_enrichment.py -q`

Expected: all tests in the file pass.

- [ ] **Step 5: Checkpoint**

Run: `git diff --check -- tests/test_skill_metadata_enrichment.py astrbot/core/skills/skill_manager.py`

Expected: no output.

---

### Task 2: Request-Scoped SkillTool

**Files:**
- Create: `tests/test_skill_tool.py`
- Create: `astrbot/core/tools/skill_tool.py`

- [ ] **Step 1: Write failing local and whitelist tests**

Create `tests/test_skill_tool.py` with tests that build temporary skills, instantiate `SkillTool(allowed_skills={skill.name: skill})`, and assert:

```python
@pytest.mark.asyncio
async def test_skill_tool_loads_local_skill_content_and_files(tmp_path, monkeypatch):
    data_dir, temp_dir, skills_root = _setup_skill_paths(monkeypatch, tmp_path)
    skill_dir = skills_root / "local-skill"
    skill_dir.joinpath("scripts").mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Local Skill\n", encoding="utf-8")
    skill_dir.joinpath("scripts", "run.py").write_text("print('ok')", encoding="utf-8")
    manager = SkillManager(skills_root=str(skills_root))
    skill = manager.get_skill("local-skill", runtime="local")
    assert skill is not None
    tool = SkillTool(
        allowed_skills={skill.name: skill},
        skill_manager=manager,
        runtime="local",
    )
    context = ContextWrapper(context=SimpleNamespace(context=None, event=SimpleNamespace(unified_msg_origin="umo")))

    result = await tool.call(context, name="local-skill")

    assert "<skill_content name=\"local-skill\" runtime=\"local\">" in result
    assert "# Local Skill" in result
    assert "<file>scripts/run.py</file>" in result


@pytest.mark.asyncio
async def test_skill_tool_rejects_unavailable_skill(tmp_path, monkeypatch):
    data_dir, temp_dir, skills_root = _setup_skill_paths(monkeypatch, tmp_path)
    skill_dir = skills_root / "local-skill"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("# Local Skill\n", encoding="utf-8")
    manager = SkillManager(skills_root=str(skills_root))
    skill = manager.get_skill("local-skill", runtime="local")
    assert skill is not None
    tool = SkillTool(
        allowed_skills={skill.name: skill},
        skill_manager=manager,
        runtime="local",
    )
    context = ContextWrapper(context=SimpleNamespace(context=None, event=SimpleNamespace(unified_msg_origin="umo")))

    result = await tool.call(context, name="missing")

    assert result == "error: Skill 'missing' is not available. Available skills: local-skill"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skill_tool.py -q`

Expected: fails because `astrbot.core.tools.skill_tool` does not exist.

- [ ] **Step 3: Implement local `SkillTool`**

Create `astrbot/core/tools/skill_tool.py` as a pydantic dataclass subclass of `FunctionTool[AstrAgentContext]` with instance fields `allowed_skills`, `skill_manager`, `runtime`, and `max_files`. Implement local loading with `Path(skill.path).read_text(encoding="utf-8")` and `skill_manager.list_local_skill_files()`.

- [ ] **Step 4: Run local tests to verify green**

Run: `uv run pytest tests/test_skill_tool.py -q`

Expected: local and whitelist tests pass.

- [ ] **Step 5: Add failing sandbox tests**

Extend `tests/test_skill_tool.py` with fake booter/context tests for sandbox-only skill reads and auxiliary listing failure. Expected output should include skill content even when file listing fails.

- [ ] **Step 6: Run sandbox tests to verify they fail**

Run: `uv run pytest tests/test_skill_tool.py -q`

Expected: sandbox tests fail because sandbox loading is not implemented.

- [ ] **Step 7: Implement sandbox loading**

Use `get_booter(context.context.context, context.context.event.unified_msg_origin)` and the booter shell to read `SkillInfo.path` and enumerate sibling files. Keep listing best-effort and return `error:` only when `SKILL.md` content cannot be read.

- [ ] **Step 8: Run SkillTool tests to verify green**

Run: `uv run pytest tests/test_skill_tool.py -q`

Expected: all SkillTool tests pass.

---

### Task 3: Agent Build Injection

**Files:**
- Modify: `tests/unit/test_astr_main_agent.py`
- Modify: `astrbot/core/astr_main_agent.py`

- [ ] **Step 1: Write failing injection tests**

Add tests under `TestEnsurePersonaAndSkills` asserting that `_ensure_persona_and_skills()` adds a tool named `skill` when skills exist, omits it when `persona["skills"] == []`, and still adds it when `persona["tools"]` is an explicit list.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_astr_main_agent.py -q`

Expected: new tests fail because `SkillTool` is not injected.

- [ ] **Step 3: Inject `SkillTool` after persona toolset construction**

Import `SkillTool`, preserve the existing persona skill filtering, then add `SkillTool(allowed_skills={skill.name: skill for skill in skills}, skill_manager=skill_manager, runtime=runtime)` to `persona_toolset` before merging it into `req.func_tool`.

- [ ] **Step 4: Run agent tests to verify green**

Run: `uv run pytest tests/unit/test_astr_main_agent.py -q`

Expected: all tests in the file pass.

---

### Task 4: Runner Activation Tracking

**Files:**
- Modify: `tests/test_tool_loop_agent_runner.py`
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py`

- [ ] **Step 1: Write failing runner tests**

Add tests that execute a `FunctionTool` named `skill`, assert `runner._activated_skills == {"local-skill"}`, and assert the next provider request includes `Activated skills this session: local-skill` without permanently appending that reminder to `runner.run_context.messages`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tool_loop_agent_runner.py -q`

Expected: new tests fail because `_activated_skills` and transient reminder injection do not exist.

- [ ] **Step 3: Implement activation tracking**

Initialize `self._activated_skills = set()` in `reset()`. After tool execution, if `func_tool_name == "skill"`, the requested `name` is non-empty, and the final result text does not start with `error:`, add the skill name.

- [ ] **Step 4: Implement transient reminder injection**

Add a helper that returns `self.run_context.messages` plus a temporary system message when `_activated_skills` is non-empty, and use it in `_iter_llm_responses()` payload construction.

- [ ] **Step 5: Run runner tests to verify green**

Run: `uv run pytest tests/test_tool_loop_agent_runner.py -q`

Expected: all runner tests pass.

---

### Task 5: Focused Regression Suite And Formatting

**Files:**
- All modified files from Tasks 1-4

- [ ] **Step 1: Run focused tests**

Run: `uv run pytest tests/test_skill_metadata_enrichment.py tests/test_skill_tool.py tests/unit/test_astr_main_agent.py tests/test_tool_loop_agent_runner.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Format Python code**

Run: `uv run ruff format astrbot/core/skills/skill_manager.py astrbot/core/tools/skill_tool.py astrbot/core/astr_main_agent.py astrbot/core/agent/runners/tool_loop_agent_runner.py tests/test_skill_metadata_enrichment.py tests/test_skill_tool.py tests/unit/test_astr_main_agent.py tests/test_tool_loop_agent_runner.py`

Expected: command exits 0.

- [ ] **Step 3: Lint modified Python files**

Run: `uv run ruff check astrbot/core/skills/skill_manager.py astrbot/core/tools/skill_tool.py astrbot/core/astr_main_agent.py astrbot/core/agent/runners/tool_loop_agent_runner.py tests/test_skill_metadata_enrichment.py tests/test_skill_tool.py tests/unit/test_astr_main_agent.py tests/test_tool_loop_agent_runner.py`

Expected: command exits 0.

- [ ] **Step 4: Check diff whitespace**

Run: `git diff --check -- astrbot/core/skills/skill_manager.py astrbot/core/tools/skill_tool.py astrbot/core/astr_main_agent.py astrbot/core/agent/runners/tool_loop_agent_runner.py tests/test_skill_metadata_enrichment.py tests/test_skill_tool.py tests/unit/test_astr_main_agent.py tests/test_tool_loop_agent_runner.py docs/superpowers/plans/2026-04-27-dedicated-skill-tool.md docs/agentHarnessUpdate/skill-system-design.md`

Expected: no output.

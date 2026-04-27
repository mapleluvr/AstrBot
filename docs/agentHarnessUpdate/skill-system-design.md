# Skill System Enhancement: Dedicated SkillTool

## Status: Design — Revised After Review

---

## 1. Motivation

### 1.1 Current State

AstrBot currently implements a **progressive disclosure** pattern for skills:

1. `SkillManager.list_skills()` scans available skills.
2. `build_skills_prompt()` injects skill names, descriptions, and file paths into the system prompt.
3. The LLM decides whether a skill is relevant.
4. The LLM reads `SKILL.md` with shell or filesystem tools.
5. The LLM follows the instructions in the file.

**Problems with this approach:**

- **No dedicated loading path**: Skill content is indistinguishable from arbitrary file reads.
- **Fragile activation**: The LLM must parse a path from the prompt and choose the right runtime tool to read it.
- **No lifecycle awareness**: The runner has no record of which skills were activated in a session.
- **Weak auxiliary file discovery**: Skill directories may include scripts, templates, and references, but the LLM only sees those files after additional exploration.

### 1.2 Goal

Give skills **first-class treatment** in the agent tool system with a dedicated `skill` tool that:

- Enforces the existing persona skill whitelist at execution time.
- Loads `SKILL.md` through a runtime-aware path.
- Lists directly available auxiliary files without bulk-loading them.
- Lets the agent runner remember which skills have been activated.
- Keeps the existing skill storage format and dashboard behavior unchanged.

### 1.3 Design Decisions

| Decision | Choice |
|----------|--------|
| Activation pattern | Dedicated request-scoped `SkillTool` exposed as tool name `skill` |
| Permission model | Keep existing Persona whitelist (`persona.skills`) |
| Runtime behavior | Runtime-aware loading: local skills from local disk; sandbox skills through the sandbox runtime |
| Tool state | Instance fields only; no class-level mutable whitelist |
| Runner tracking | Track successful `skill` calls from tool name + arguments, not result metadata |
| Prompt migration | Update `build_skills_prompt()` in the same rollout as tool injection |
| Scope | Skill loading, prompt update, runner tracking, and tests; no skill lifecycle changes |

---

## 2. Architecture

### 2.1 Component Diagram

```
build_main_agent()
  |
  +-- _decorate_llm_request()
      |
      +-- _ensure_persona_and_skills()
          |
          +-- resolve persona
          +-- SkillManager.list_skills(active_only=True, runtime=runtime)
          +-- filter by persona.skills
          +-- build_skills_prompt(filtered_skills) -> system prompt uses SkillTool
          +-- build persona_toolset from persona.tools or plugin tools
          +-- inject request-scoped SkillTool if filtered_skills is not empty

ToolLoopAgentRunner
  |
  +-- reset(): clears _activated_skills
  +-- _handle_function_tools():
      |
      +-- executes normal tool loop
      +-- if tool name is "skill" and result is not an error:
          +-- record args["name"] in _activated_skills
  +-- _iter_llm_responses():
      |
      +-- adds a transient <system_reminder> with activated skill names

SkillTool
  |
  +-- name = "skill"
  +-- allowed_skills: dict[str, SkillInfo]
  +-- skill_manager: SkillManager
  +-- runtime: "local" | "sandbox" | "none"
  +-- call(context, name):
      |
      +-- validate name and whitelist
      +-- load SKILL.md from local disk or sandbox runtime
      +-- list auxiliary files for the same runtime
      +-- return formatted skill content
```

### 2.2 File Changes

| File | Action | Description |
|------|--------|-------------|
| `astrbot/core/tools/skill_tool.py` | **NEW** | Request-scoped `SkillTool` implementation |
| `astrbot/core/astr_main_agent.py` | MODIFY | Instantiate and inject `SkillTool` after persona toolset construction |
| `astrbot/core/skills/skill_manager.py` | MODIFY | Add runtime-aware single-skill lookup and local auxiliary file listing |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | MODIFY | Track successful skill activations and inject transient reminders |
| `tests/test_skill_tool.py` | **NEW** | Unit tests for `SkillTool` local behavior and whitelist enforcement |
| `tests/test_skill_manager_skill_lookup.py` | **NEW** | Unit tests for single-skill lookup and auxiliary file listing |
| `tests/test_skill_tool_agent_integration.py` | **NEW** | Agent build and runner tracking tests |

No `func_tool_manager.py` change is needed. `SkillTool` carries per-request state, so it must not be cached as a global builtin instance.

---

## 3. Detailed Design

### 3.1 SkillTool

**File**: `astrbot/core/tools/skill_tool.py`

Use the existing `FunctionTool` pattern: dataclass fields, `parameters`, and `call()`.

```python
from dataclasses import field

from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.skills.skill_manager import SkillInfo, SkillManager


@dataclass
class SkillTool(FunctionTool[AstrAgentContext]):
    name: str = "skill"
    description: str = (
        "Load a specialized skill by name. Use this before following a skill's "
        "instructions. Available skills are listed in the system prompt."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the skill to load.",
                }
            },
            "required": ["name"],
        }
    )
    allowed_skills: dict[str, SkillInfo] = field(default_factory=dict, repr=False)
    skill_manager: SkillManager = field(default_factory=SkillManager, repr=False)
    runtime: str = "local"
    max_files: int = 20

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        name: str,
    ) -> ToolExecResult:
        raise NotImplementedError("See SkillTool execution flow below.")
```

Important implementation constraints:

- `allowed_skills` must be an instance field. Do not store persona-specific state on the class.
- Do not decorate this class with `@builtin_tool` unless the builtin manager supports uncached per-request instances. The current manager caches builtin instances by class, which is wrong for persona whitelists.
- `call()` returns text or `CallToolResult` only. Do not rely on a custom `metadata` field because the current runner discards unsupported result metadata.

### 3.2 SkillTool Execution

`call(context, name)` uses this flow:

1. Validate `name` is a non-empty string.
2. Look up `allowed_skills[name]`.
3. If missing, return `error: Skill '<name>' is not available. Available skills: <comma-separated names>`.
4. Load skill content using the current runtime.
5. List auxiliary files in the same runtime, capped by `max_files`.
6. Return formatted content:

```xml
<skill_content name="{name}" runtime="{runtime}">
{raw SKILL.md content}

<skill_files>
  <file>{path}</file>
  <file>{another_path}</file>
</skill_files>
</skill_content>
```

Runtime-specific behavior:

| Runtime | Loading behavior |
|---------|------------------|
| `local` | Read from the local skill directory using `pathlib.Path` |
| `sandbox` | Read the `SkillInfo.path` from the sandbox via the active booter and existing file-read helpers |
| `none` | Load local skill text only; keep the existing prompt warning that shell/Python execution is unavailable |

Sandbox behavior is required because `SkillManager.list_skills(runtime="sandbox")` can return `sandbox_only` skills with no local `SKILL.md`. Returning an error for those skills would break the main reason the cached sandbox skill list exists.

Sandbox auxiliary file listing should be best-effort:

- Use the sandbox runtime to enumerate files under the skill directory.
- Skip `SKILL.md` and directories.
- Return normalized sandbox-relative paths.
- If listing fails, still return the skill content with an empty `<skill_files>` block and a short warning line.

### 3.3 SkillManager Additions

**File**: `astrbot/core/skills/skill_manager.py`

Add single-skill lookup that reuses existing `list_skills()` filtering and runtime behavior:

```python
def get_skill(
    self,
    name: str,
    *,
    runtime: str = "local",
    show_sandbox_path: bool = True,
) -> SkillInfo | None:
    for skill in self.list_skills(
        active_only=True,
        runtime=runtime,
        show_sandbox_path=show_sandbox_path,
    ):
        if skill.name == name:
            return skill
    return None
```

Add local auxiliary file listing:

```python
def list_local_skill_files(self, name: str, max_files: int = 20) -> list[str]:
    skill = self.get_skill(name, runtime="local", show_sandbox_path=False)
    if skill is None or not skill.local_exists:
        return []
    skill_md = Path(skill.path)
    skill_dir = skill_md.parent
    files: list[str] = []
    for file_path in sorted(skill_dir.rglob("*")):
        if not file_path.is_file() or file_path.name == "SKILL.md":
            continue
        files.append(file_path.relative_to(skill_dir).as_posix())
        if len(files) >= max_files:
            break
    return files
```

Keep sandbox file listing in `SkillTool`, not `SkillManager`, because it needs the active runtime context and booter.

### 3.4 Agent Build Integration

**File**: `astrbot/core/astr_main_agent.py`

The current `_ensure_persona_and_skills()` filters skills before building the persona toolset. Keep that filtering, but inject `SkillTool` after the toolset is built.

Pseudo-flow:

```python
runtime = cfg.get("computer_use_runtime", "local")
skill_manager = SkillManager()
skills = skill_manager.list_skills(active_only=True, runtime=runtime)

if persona and persona.get("skills") is not None:
    if not persona["skills"]:
        skills = []
    else:
        allowed = set(persona["skills"])
        skills = [skill for skill in skills if skill.name in allowed]

if skills:
    req.system_prompt += f"\n{build_skills_prompt(skills)}\n"

persona_toolset = _build_persona_toolset(req, persona, tmgr)

if skills:
    persona_toolset.add_tool(
        SkillTool(
            allowed_skills={skill.name: skill for skill in skills},
            skill_manager=skill_manager,
            runtime=runtime,
        )
    )
```

Persona rules:

- `persona["skills"] is None`: all active skills are available.
- `persona["skills"] == []`: no skills are available, and `SkillTool` is not added.
- `persona["skills"] == ["a", "b"]`: only those skills are available.
- `persona["tools"]` does not control skill access. If skills are allowed, `SkillTool` is injected even when `persona["tools"]` is an explicit list.

This keeps skill permissions independent from plugin/tool permissions and avoids requiring every persona to explicitly list the internal `skill` loader tool.

### 3.5 Prompt Update

**File**: `astrbot/core/skills/skill_manager.py`

Update `build_skills_prompt()` at the same time `SkillTool` is injected.

The skill rules should say:

- The listed skills are the complete inventory for this session.
- Before using a skill, call the `skill` tool with the skill name.
- Do not use shell or filesystem tools to read `SKILL.md` directly unless the `skill` tool is unavailable or returns an error.
- Load only auxiliary files that are referenced by the skill content or are needed for the task.

This replaces the current mandatory shell-read instruction. Keeping the old instruction during rollout would cause the LLM to keep bypassing `SkillTool`, which would also bypass activation tracking.

### 3.6 Agent Runner Integration

**File**: `astrbot/core/agent/runners/tool_loop_agent_runner.py`

Track activated skills without relying on tool-result metadata.

In `reset()`:

```python
self._activated_skills: set[str] = set()
```

After a tool call completes in `_handle_function_tools()`:

```python
if func_tool_name == "skill":
    skill_name = str(func_tool_args.get("name") or "").strip()
    if skill_name and _final_resp is not None and not getattr(_final_resp, "isError", False):
        result_text = _text_from_call_tool_result(_final_resp)
        if not result_text.lstrip().startswith("error:"):
            self._activated_skills.add(skill_name)
```

Inject a transient reminder into the next LLM request. Do not append it permanently to `self.run_context.messages`.

```python
def _messages_with_skill_reminder(self) -> list[Message]:
    messages = list(self.run_context.messages)
    if self._activated_skills:
        names = ", ".join(sorted(self._activated_skills))
        messages.append(
            Message(
                role="system",
                content=(
                    "<system_reminder>"
                    f"Activated skills this session: {names}. "
                    "Call the `skill` tool again if you need to reload a skill's instructions."
                    "</system_reminder>"
                ),
            )
        )
    return messages
```

Then `_iter_llm_responses()` should sanitize and send `_messages_with_skill_reminder()` instead of `self.run_context.messages` directly.

### 3.7 Error Handling

| Scenario | Behavior |
|----------|----------|
| Skill name is empty or not a string | Return `error: Skill name must be a non-empty string.` |
| Skill name not in whitelist | Return `error: Skill '<name>' is not available. Available skills: <comma-separated names>` |
| Local `SKILL.md` missing | Return `error: Skill '<name>' is registered but SKILL.md is missing: <path>` |
| Sandbox skill path missing | Return `error: Skill '<name>' is registered in sandbox but SKILL.md could not be read: <path>` |
| Sandbox runtime unavailable | Return `error: Sandbox runtime is not ready; cannot load sandbox skill '<name>'.` |
| Auxiliary listing fails | Return skill content and include a warning before `<skill_files>` |
| Persona disables all skills | Do not add `SkillTool`; do not add the skills prompt |

---

## 4. Data Flow

```
User message -> build_main_agent()
    |
    +-- _ensure_persona_and_skills()
        |
        +-- SkillManager.list_skills(active_only=True, runtime=runtime)
        +-- filter by persona.skills
        +-- build_skills_prompt(filtered_skills)
        +-- create persona_toolset
        +-- add SkillTool(allowed_skills=filtered_skills, runtime=runtime)
    |
    +-- ToolLoopAgentRunner.step()
        |
        +-- LLM receives skill inventory + `skill` tool
        +-- LLM calls skill(name="example")
        +-- SkillTool validates whitelist
        +-- SkillTool loads SKILL.md from local disk or sandbox runtime
        +-- SkillTool returns skill content + auxiliary file list
        +-- runner records "example" as activated
        +-- next request includes transient activated-skills reminder
```

---

## 5. Testing Strategy

| Layer | What to Test | File |
|-------|--------------|------|
| Unit: SkillTool local | Valid name returns content and local auxiliary file list | `tests/test_skill_tool.py` |
| Unit: SkillTool whitelist | Invalid or disallowed name returns an error with available names | `tests/test_skill_tool.py` |
| Unit: SkillTool sandbox | `sandbox_only` skill loads through a fake booter; missing sandbox path returns an error | `tests/test_skill_tool.py` |
| Unit: SkillManager | `get_skill()` respects runtime and active status; `list_local_skill_files()` is sorted and excludes `SKILL.md` | `tests/test_skill_manager_skill_lookup.py` |
| Integration: Agent build | `SkillTool` is added when skills are allowed, omitted when `persona.skills == []`, and still added when `persona.tools` is explicit | `tests/test_skill_tool_agent_integration.py` |
| Integration: Prompt | `build_skills_prompt()` instructs `skill` tool use and no longer mandates shell-reading `SKILL.md` | `tests/test_skill_tool_agent_integration.py` |
| Integration: Runner | Successful `skill` call updates `_activated_skills`; failed calls do not; next provider request includes transient reminder | `tests/test_skill_tool_agent_integration.py` |
| Mode: skills-like schema | `skill` works when `tool_schema_mode == "skills_like"` and the runner resolves param-only schemas | `tests/test_skill_tool_agent_integration.py` |

Test fixtures should include:

- A local skill with `SKILL.md` and one auxiliary file.
- A disabled local skill.
- A sandbox-only skill returned by fake sandbox cache data.
- A persona with `skills=None`, `skills=[]`, and a named whitelist.

---

## 6. Non-Goals

- New skill storage locations beyond existing `SkillManager` discovery.
- Remote skill fetching.
- New global permission rules beyond persona skill whitelists.
- WebUI changes for skill management.
- Neo skill lifecycle changes such as candidate promotion, release syncing, or payload authoring.
- Forced context compaction protection for loaded skill content.

Sandbox support is in scope only for reading already-discovered skill documents and listing their auxiliary files.

---

## 7. Rollout

1. **Phase 1**: Add `SkillManager.get_skill()` and `list_local_skill_files()` with tests.
2. **Phase 2**: Add request-scoped `SkillTool` with local and sandbox loading tests.
3. **Phase 3**: Inject `SkillTool` in `_ensure_persona_and_skills()` and update `build_skills_prompt()` in the same change.
4. **Phase 4**: Add runner activation tracking and transient reminders.
5. **Phase 5**: Run full unit tests and targeted manual checks in local, none, and sandbox runtimes.

### Backward Compatibility

- Existing skill directories and `SKILL.md` files continue to work unchanged.
- Persona `skills` semantics are preserved.
- Existing shell/file-read tools remain available when computer use is enabled, but the skills prompt should prefer the dedicated `skill` tool for loading `SKILL.md`.
- If `SkillTool` returns an error, the LLM can report the error or fall back to normal file tools only when those tools are available and permitted.

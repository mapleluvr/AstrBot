# Fork Appendix

> This document describes the enhancements in the `feature/persistent-subagent-runtime` branch of [mapleluvr/AstrBot](https://github.com/mapleluvr/AstrBot), forked from [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot).

---

## English

### 1. Persistent Subagent Runtime

Subagents can now run in a **persistent** mode alongside the legacy **handoff** model. A persistent subagent maintains its own long-lived conversation context and can be messaged directly via management tools.

- **Lifecycle context**: each persistent subagent carries its own system prompt, LLM provider bindings, and persona-derived tool set.
- **Management tools**: the main agent receives `subagent_persistent_*` tools to interact with persistent presets — send prompts, review conversation history, check status, reset context, or shut down a subagent.
- **Runtime gating**: tool injection respects the `subagent_orchestrator.runtime.enable` config flag; when disabled management tools are omitted.

### 2. Dedicated Skill Tool (`skill`)

Skills have first-class treatment via a request-scoped `skill` tool instead of the previous FileReadTool / shell-read path.

- **Runtime-aware loading**: local skills are read from disk; sandbox-only skills are loaded through the active sandbox booter's filesystem.
- **Permission model**: respects the existing `persona.skills` whitelist; the tool is omitted when all skills are disabled.
- **Auxiliary file discovery**: each skill activation returns the `SKILL.md` content plus a `<skill_files>` block listing every auxiliary file with directly usable `path` and skill-relative `relative` paths.
- **Session tracking**: `ToolLoopAgentRunner` records successfully activated skills and injects a transient `<system_reminder>` reminding the LLM of active skills.
- **Prompt migration**: `build_skills_prompt()` now instructs the LLM to call the `skill` tool instead of reading `SKILL.md` via shell/file tools.

### 3. Dispatching Subagents Skill

A built-in `dispatching-subagents` skill provides reusable instructions for orchestrating multi-subagent workflows — splitting complex tasks across parallel or sequenced subagent calls.

### 4. Dashboard — SubAgent Management

The WebUI SubAgentPage now exposes a **table view** for managing persistent subagent presets:
- Column display for name, model, output mode, and status.
- Foundation for future in-UI subagent lifecycle controls.

---

## 中文

### 1. 持久化子智能体运行时

子智能体现在可以在传统的 **handoff（交接）** 模式之外以 **persistent（持久化）** 模式运行。持久化子智能体维护自己的长期对话上下文，可通过管理工具直接向其发送消息。

- **生命周期上下文**：每个持久化子智能体拥有自己的系统提示词、LLM 提供商绑定和基于 persona 的工具集。
- **管理工具**：主智能体会获得 `subagent_persistent_*` 工具，用于与持久化预设交互——发送提示词、查看对话历史、检查状态、重置上下文或关闭子智能体。
- **运行时开关**：工具注入受 `subagent_orchestrator.runtime.enable` 配置标志控制；禁用时管理工具不会被注入。

### 2. 专用 Skill 工具 (`skill`)

技能现在通过一个请求级作用域（request-scoped）的 `skill` 工具获得一等公民待遇，取代了此前的 FileReadTool / shell-read 路径。

- **运行时感知加载**：本地技能从磁盘读取；仅存在于沙箱中的技能通过活跃沙箱 booter 的文件系统加载。
- **权限模型**：遵循现有的 `persona.skills` 白名单；所有技能被禁用时该工具不会被注入。
- **辅助文件发现**：每次技能激活返回 `SKILL.md` 内容以及一个 `<skill_files>` 块，列出所有辅助文件，包含可直接使用的 `path` 和相对技能目录的 `relative` 路径。
- **会话追踪**：`ToolLoopAgentRunner` 记录成功激活的技能，并在后续 LLM 请求中注入临时的 `<system_reminder>` 提示已激活的技能。
- **提示词迁移**：`build_skills_prompt()` 现在引导 LLM 调用 `skill` 工具，而不是通过 shell/file 工具读取 `SKILL.md`。

### 3. 分发子智能体技能

内建的 `dispatching-subagents` 技能提供了可复用的多子智能体工作流协调指令——将复杂任务拆分到并行或顺序的子智能体调用中。

### 4. WebUI — 子智能体管理

WebUI 的 SubAgentPage 现在新增了**表格视图**用于管理持久化子智能体预设：
- 列显示：名称、模型、输出模式、状态。
- 为未来的界面内子智能体生命周期控制奠定基础。

---

## Files Changed (Summary)

| Path | Description |
|------|-------------|
| `astrbot/core/subagent_runtime.py` | Persistent subagent lifecycle, context, and tool execution |
| `astrbot/core/tools/subagent_runtime_tools.py` | `subagent_persistent_*` management tool implementations |
| `astrbot/core/tools/skill_tool.py` | Request-scoped `skill` tool with local/sandbox loading |
| `astrbot/core/skills/skill_manager.py` | Single-skill lookup, auxiliary file listing, prompt migration |
| `astrbot/core/astr_main_agent.py` | SkillTool injection, persistent preset tool gating |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | Skill activation tracking and transient reminders |
| `dashboard/src/views/SubAgentPage.vue` | Table view for persistent subagent presets |
| `data/skills/dispatching-subagents/SKILL.md` | Dispatching subagents skill definition |
| `tests/test_skill_tool.py` | SkillTool unit tests (local, sandbox, whitelist) |
| `tests/unit/test_astr_main_agent.py` | Agent build injection tests |
| `tests/test_tool_loop_agent_runner.py` | Runner activation tracking tests |
| `docs/agentHarnessUpdate/skill-system-design.md` | Detailed skill system enhancement design doc |

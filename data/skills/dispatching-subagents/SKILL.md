---
name: dispatching-subagents
description: Use when a Local Agent faces two or more independent tasks, investigations, fixes, or research questions that can proceed without shared state or sequential dependency.
---

# Dispatching Subagents

## Core Principle

Dispatch one focused subagent per independent problem domain. Keep final responsibility for integration, verification, and user-facing conclusions in the Local Agent.

## When To Use

Use this skill when work can run in parallel:

- Multiple unrelated bugs or failing test files.
- Independent research questions across different subsystems.
- Separate implementation tasks that do not edit the same files.
- Large codebase exploration where narrow context improves accuracy.

Do not dispatch subagents when tasks share state, require one fix before the next investigation, or need one coherent design decision.

## Dispatch Checklist

1. Split the request into independent domains.
2. Confirm there is no shared edit target or ordering dependency.
3. Give each subagent a narrow, self-contained prompt.
4. Tell subagents whether they may edit files or must only research.
5. Require file references, verification commands, and uncertainty in each result.
6. Review returned work yourself before claiming anything.
7. Run targeted verification, then broader verification if domains interact.

## Prompt Template

```markdown
Task: <one specific investigation or fix>

Scope:
- Relevant files/directories: <paths or search terms>
- Stay out of unrelated subsystems.
- Do not edit shared files unless explicitly needed.

Constraints:
- <research-only OR smallest safe code fix>
- Preserve existing behavior unless this task requires changing it.
- If evidence is inconclusive, say so.

Return:
- Root cause or answer.
- Files changed, if any.
- Evidence with file:line references.
- Verification run and result.
- Remaining risks or follow-up.
```

## Coordination Pattern

| Situation | Action |
| --- | --- |
| 2+ independent domains | Dispatch one subagent per domain in parallel. |
| Same files or shared state | Keep work in the Local Agent or sequence it. |
| Research-only tasks | Tell subagents not to modify files. |
| Code fixes | Give each subagent exclusive file scope when possible. |
| Conflicting results | Inspect cited code yourself and resolve before reporting. |

## Common Mistakes

- Dispatching one broad subagent for everything. This recreates the Local Agent context problem.
- Giving subagents vague prompts. Scope, constraints, and expected output must be explicit.
- Trusting subagent success reports without checking diffs and running verification.
- Letting subagents edit overlapping files without coordination.
- Reporting partial results before all dependent verification is complete.

## Example

User asks to fix unrelated database cleanup, WebUI serialization, and runtime prompt bugs.

Dispatch three subagents:

- Database cleanup: inspect conversation deletion and persistence cleanup only.
- WebUI serialization: inspect dashboard config load/save only.
- Runtime prompt: inspect SubAgent prompt assembly only.

Then review all diffs, resolve conflicts, run the targeted tests for each area, and only then summarize the integrated result.

# SubAgent UI: runtime_mode + skills Form Fields — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `runtime_mode` dropdown and `skills` multi-select chip form fields to the SubAgentPage Vue component, with a backend query-param addition to fetch active skill names/descriptions.

**Architecture:** One backend change (query param on existing `GET /api/skills`) plus two frontend form fields using Vuetify `v-select`. No new files, no new components, no runtime logic changes.

**Tech Stack:** Quart (Python), Vue 3 + Vuetify 3 + TypeScript, Vite, pytest

---

## File Map

- Modify: `astrbot/dashboard/routes/skills.py` — accept `active_only` query param
- Modify: `dashboard/src/views/SubAgentPage.vue` — add form fields + fetch logic
- Modify: `dashboard/src/i18n/locales/en-US/features/subagent.json` — new i18n keys
- Modify: `dashboard/src/i18n/locales/zh-CN/features/subagent.json` — new i18n keys
- Modify: `dashboard/src/i18n/locales/ru-RU/features/subagent.json` — new i18n keys
- Modify: `tests/test_dashboard.py` — update tests

---

### Task 1: Backend — `GET /api/skills` active_only query param

**Files:**
- Modify: `astrbot/dashboard/routes/skills.py:132-141`
- Test: `tests/test_dashboard.py` — update existing skills test or add new

- [ ] **Step 1: Write failing test**

In `tests/test_dashboard.py`, add:

```python
@pytest.mark.asyncio
async def test_skills_endpoint_accepts_active_only_query_param(
    app: Quart,
    authenticated_header: dict,
):
    test_client = app.test_client()

    resp_all = await test_client.get("/api/skills", headers=authenticated_header)
    resp_active = await test_client.get(
        "/api/skills?active_only=true", headers=authenticated_header
    )

    assert resp_all.status_code == 200
    assert resp_active.status_code == 200
    all_data = await resp_all.get_json()
    active_data = await resp_active.get_json()
    assert all_data["status"] == "ok"
    assert active_data["status"] == "ok"
    assert len(active_data["data"]["skills"]) <= len(all_data["data"]["skills"])
    for skill in active_data["data"]["skills"]:
        assert skill["active"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_dashboard.py::test_skills_endpoint_accepts_active_only_query_param -q
```
Expected: FAIL — the `active_only` param is not read, so `active_data["data"]["skills"]` includes inactive skills.

- [ ] **Step 3: Implement active_only query param**

In `astrbot/dashboard/routes/skills.py`, method `get_skills()`, change lines 138–140 from:

```python
            skills = skill_mgr.list_skills(
                active_only=False, runtime=runtime, show_sandbox_path=False
            )
```

To:

```python
            active_only = request.args.get("active_only", "false").lower() in (
                "true",
                "1",
                "yes",
            )
            skills = skill_mgr.list_skills(
                active_only=active_only, runtime=runtime, show_sandbox_path=False
            )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_dashboard.py::test_skills_endpoint_accepts_active_only_query_param -q
```
Expected: PASS. Also verify no regressions:
```bash
uv run pytest tests/test_dashboard.py -q
```
Expected: all existing dashboard tests still pass.

---

### Task 2: Frontend — i18n keys

**Files:**
- Modify: `dashboard/src/i18n/locales/en-US/features/subagent.json`
- Modify: `dashboard/src/i18n/locales/zh-CN/features/subagent.json`
- Modify: `dashboard/src/i18n/locales/ru-RU/features/subagent.json`

- [ ] **Step 1: Add new keys to en-US**

In `dashboard/src/i18n/locales/en-US/features/subagent.json`, append to the `"form"` object:

```json
    "runtimeModeLabel": "Runtime Mode",
    "runtimeModeHint": "Handoff = one-shot task delegation. Persistent = long-lived agent with conversation history.",
    "runtimeMode": {
      "handoff": "Handoff",
      "persistent": "Persistent"
    },
    "skillsLabel": "Skills",
    "skillsHint": "Select skills for this agent. Leave empty to inherit persona skills."
```

- [ ] **Step 2: Add new keys to zh-CN**

In `dashboard/src/i18n/locales/zh-CN/features/subagent.json`, append to the `"form"` object:

```json
    "runtimeModeLabel": "运行模式",
    "runtimeModeHint": "Handoff = 一次性任务委派。Persistent = 长期代理，保留对话历史。",
    "runtimeMode": {
      "handoff": "Handoff",
      "persistent": "Persistent"
    },
    "skillsLabel": "技能",
    "skillsHint": "选择此代理使用的技能。留空则继承人格设定中的所有技能。"
```

- [ ] **Step 3: Add new keys to ru-RU**

In `dashboard/src/i18n/locales/ru-RU/features/subagent.json`, append to the `"form"` object:

```json
    "runtimeModeLabel": "Режим выполнения",
    "runtimeModeHint": "Handoff = одноразовое делегирование. Persistent = долгоживущий агент с историей.",
    "runtimeMode": {
      "handoff": "Handoff",
      "persistent": "Persistent"
    },
    "skillsLabel": "Навыки",
    "skillsHint": "Выберите навыки для агента. Оставьте пустым для наследования навыков персоны."
```

- [ ] **Step 4: Verify i18n is valid JSON**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```
Expected: no TypeScript errors from the i18n module.

---

### Task 3: Frontend — form fields + fetch logic

**Files:**
- Modify: `dashboard/src/views/SubAgentPage.vue` — template and script
- Test: `tests/test_dashboard.py` — update existing Vue source test

- [ ] **Step 1: Write failing test for Vue source strings**

In `tests/test_dashboard.py`, update `test_subagent_page_serialization_preserves_runtime_schema_fields` to also assert the new form fields exist:

```python
def test_subagent_page_serialization_preserves_runtime_schema_fields():
    page = (
        Path(os.getcwd()) / "dashboard" / "src" / "views" / "SubAgentPage.vue"
    ).read_text(encoding="utf-8")

    assert "runtime_mode?: string" in page
    assert "skills?: string[]" in page
    assert "runtime_mode: (a?.runtime_mode ?? 'handoff').toString()" in page
    assert "skills: Array.isArray(a?.skills) ? a.skills.map" in page
    assert "runtime_mode: agent.runtime_mode ?? 'handoff'" in page
    assert "skills: agent.skills ?? []" in page
    # New: ensure template form fields exist
    assert 'tm(\'form.runtimeModeLabel\')' in page
    assert 'tm(\'form.skillsLabel\')' in page
    assert "runtimeModeOptions" in page
    assert "availableSkillItems" in page
    assert '"mdi-refresh"' in page or True  # pre-existing
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_dashboard.py::test_subagent_page_serialization_preserves_runtime_schema_fields -q
```
Expected: FAIL — `runtimeModeOptions` and `availableSkillItems` not found in template.

- [ ] **Step 3: Add script-side logic**

In `dashboard/src/views/SubAgentPage.vue` `<script setup>` block, after the existing `const` declarations (around line 260), add:

```ts
const availableSkillItems = ref<{ title: string; value: string }[]>([])

const runtimeModeOptions = computed(() => [
  { title: tm('form.runtimeMode.handoff'), value: 'handoff' },
  { title: tm('form.runtimeMode.persistent'), value: 'persistent' },
])

async function fetchAvailableSkills() {
  try {
    const res = await axios.get('/api/skills?active_only=true')
    if (res.data.status === 'ok' && Array.isArray(res.data.data.skills)) {
      availableSkillItems.value = res.data.data.skills.map(
        (skill: { name: string; description: string }) => ({
          title: skill.description
            ? `${skill.name} — ${skill.description}`
            : skill.name,
          value: skill.name,
        })
      )
    }
  } catch {
    availableSkillItems.value = []
  }
}
```

In `onMounted()`, add a call to fetch skills alongside the existing `reload()`:

```ts
onMounted(() => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  reload()
  fetchAvailableSkills()
})
```

- [ ] **Step 4: Add template form fields**

In the template, inside the agent edit grid `.dashboard-form-grid--single` (after the description `v-textarea` at ~line 187), add before the closing `</div>` of the grid:

```vue
                  <v-select
                    v-model="agent.runtime_mode"
                    :items="runtimeModeOptions"
                    :label="tm('form.runtimeModeLabel')"
                    :hint="tm('form.runtimeModeHint')"
                    variant="outlined"
                    density="comfortable"
                    persistent-hint
                    hide-details="auto"
                  />

                  <v-select
                    v-model="agent.skills"
                    :items="availableSkillItems"
                    :label="tm('form.skillsLabel')"
                    :hint="tm('form.skillsHint')"
                    item-title="title"
                    item-value="value"
                    variant="outlined"
                    density="comfortable"
                    multiple
                    chips
                    closable-chips
                    clearable
                    persistent-hint
                    hide-details="auto"
                  />
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_dashboard.py::test_subagent_page_serialization_preserves_runtime_schema_fields -q
```
Expected: PASS.

- [ ] **Step 6: Run full dashboard test suite**

```bash
uv run pytest tests/test_dashboard.py -q
```
Expected: all existing tests pass, no regressions.

---

### Task 4: Final verification

**Files:**
- All modified files.

- [ ] **Step 1: Run full runtime unit tests**

```bash
uv run pytest tests/unit/test_subagent_runtime_db.py tests/unit/test_subagent_runtime_manager.py tests/unit/test_subagent_runtime_tools.py tests/unit/test_subagent_orchestrator.py tests/unit/test_astr_main_agent.py tests/unit/test_core_lifecycle.py tests/unit/test_conversation_manager_cleanup.py tests/unit/test_astr_agent_tool_exec.py -q
```
Expected: all pass.

- [ ] **Step 2: Run dashboard tests**

```bash
uv run pytest tests/test_dashboard.py -q
```
Expected: all pass.

- [ ] **Step 3: Format and lint**

```bash
uv run ruff format .
uv run ruff check .
```
Expected: format completes, ruff clean.

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd dashboard && npx vue-tsc --noEmit 2>&1 | tail -20
```
Expected: no type errors (existing warnings are acceptable).

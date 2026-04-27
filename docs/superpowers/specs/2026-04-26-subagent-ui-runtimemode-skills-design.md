# SubAgent UI: runtime_mode + skills Form Fields

> **Scope:** Add `runtime_mode` and `skills` form controls to the SubAgentPage dashboard,
> plus a minimal backend query-param addition so the UI can fetch active skill names.

## Problem

The persistent SubAgent runtime feature (Tasks 1–6) is fully implemented in the backend, but the
dashboard SubAgentPage has no form fields for `runtime_mode` or `skills`. Users must edit
`cmd_config.json` manually to set persistent mode or assign skills. The TypeScript types and
serialization already preserve both fields, but the template provides zero inputs.

## Backend Change

### `GET /api/skills` – accept `active_only` query parameter

**File:** `astrbot/dashboard/routes/skills.py`, method `get_skills()`, ~line 132

- Read query param `active_only` (string `"true"` / `"false"`), default `"false"`.
- Pass `active_only=active_only` to `SkillManager.list_skills()`.
- Existing `skill.__dict__` serialization already includes `name`, `description`, and `active`
  — no response-format change needed.

### `GET /api/skills?active_only=true`

Frontend uses this to populate the skills multi-select. Each returned skill object is:

```json
{
  "name": "summarize",
  "description": "Summarize long text into bullet points.",
  "active": true,
  "path": "...",
  "source_type": "local_only",
  "source_label": "local",
  "local_exists": true,
  "sandbox_exists": false
}
```

Frontend maps `{ title: "summarize — Summarize long text...", value: "summarize" }`.

## Frontend Changes

### `dashboard/src/views/SubAgentPage.vue`

**Script additions:**
- New `ref` for fetched skills: `const availableSkillItems = ref<{title: string; value: string}[]>([])`
- New computed for runtime mode options:
  ```ts
  const runtimeModeOptions = computed(() => [
    { title: tm('form.runtimeMode.handoff'), value: 'handoff' },
    { title: tm('form.runtimeMode.persistent'), value: 'persistent' },
  ])
  ```
- `loadConfig()` also fetches `GET /api/skills?active_only=true` and populates
  `availableSkillItems` with `{ title: name + ' — ' + description, value: name }`.
  Fetch failure is non-fatal; user can still type skill names (but no autocomplete).

**Template additions** (inside `.dashboard-form-grid--single`, after description textarea):

1. `runtime_mode` — `v-select` dropdown:
   ```vue
   <v-select
     v-model="agent.runtime_mode"
     :items="runtimeModeOptions"
     :label="tm('form.runtimeModeLabel')"
     :hint="tm('form.runtimeModeHint')"
     variant="outlined"
     density="comfortable"
     hide-details="auto"
   />
   ```

2. `skills` — `v-select` with multiple chips:
   ```vue
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
     hide-details="auto"
   />
   ```

### i18n — `features/subagent.json`

New keys added under `"form"` (en-US, zh-CN, ru-RU):

| Key | en-US |
|-----|-------|
| `runtimeModeLabel` | `Runtime Mode` |
| `runtimeModeHint` | `Handoff = one-shot task delegation. Persistent = long-lived agent with conversation history.` |
| `runtimeMode.handoff` | `Handoff` |
| `runtimeMode.persistent` | `Persistent` |
| `skillsLabel` | `Skills` |
| `skillsHint` | `Select skills for this agent. Leave empty to inherit persona skills.` |

Existing keys are **not** modified.

## Testing

### `tests/test_dashboard.py`

- Ensure the `subagent_page_serialization_preserves_runtime_schema_fields` test still
  passes (or update it to reflect new template shape).
- Ensure the round-trip config test (`test_subagent_config_preserves_runtime_schema_fields`)
  still preserves `runtime_mode` and `skills` after a save.

### `tests/unit/test_subagent_runtime_tools.py` / `test_subagent_runtime_manager.py`

No changes — behavior is unchanged.

## No Changes To

- `astrbot/core/` — no runtime logic changes
- `astrbot/dashboard/routes/subagent.py` — config load/save already preserves both fields
- `dashboard/src/components/` — using only Vuetify built-ins, no new shared components
- Other `dashboard/src/views/` pages

## Verification

```bash
uv run pytest tests/test_dashboard.py -q
uv run ruff check astrbot/dashboard/routes/skills.py dashboard/src/views/SubAgentPage.vue
uv run ruff format .
```

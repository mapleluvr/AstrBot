<template>
  <div class="dashboard-page agent-group-page" :class="{ 'is-dark': isDark }">
    <v-container fluid class="dashboard-shell pa-4 pa-md-6">
      <div class="dashboard-header">
        <div class="dashboard-header-main">
          <div class="dashboard-eyebrow">{{ tm('header.eyebrow') }}</div>
          <h1 class="dashboard-title">{{ tm('page.title') }}</h1>
          <p class="dashboard-subtitle">{{ tm('page.subtitle') }}</p>
        </div>

        <div class="dashboard-header-actions">
          <v-btn variant="text" color="primary" prepend-icon="mdi-refresh" :loading="loading" @click="reload">
            {{ tm('actions.refresh') }}
          </v-btn>
          <v-btn variant="tonal" color="primary" prepend-icon="mdi-content-save" :loading="saving" @click="save">
            {{ tm('actions.save') }}
          </v-btn>
        </div>
      </div>

      <div v-if="hasUnsavedChanges" class="unsaved-banner">
        <v-icon size="18" color="warning">mdi-alert-circle-outline</v-icon>
        <span>{{ tm('messages.unsavedChangesNotice') }}</span>
      </div>

      <div class="dashboard-section-head">
        <div>
          <div class="dashboard-section-title">{{ tm('section.presets') }}</div>
          <div class="dashboard-section-subtitle">{{ cfg.presets.length }}</div>
        </div>
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="addPreset">
          {{ tm('actions.addPreset') }}
        </v-btn>
      </div>

      <div v-if="cfg.presets.length === 0" class="dashboard-card dashboard-card--padded empty-card">
        <div class="empty-wrap">
          <v-icon icon="mdi-account-group-outline" size="60" class="mb-4" />
          <div class="empty-title">{{ tm('empty.title') }}</div>
          <div class="dashboard-empty mb-4">{{ tm('empty.subtitle') }}</div>
          <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="addPreset">
            {{ tm('empty.action') }}
          </v-btn>
        </div>
      </div>

      <div v-else class="preset-layout">
        <aside class="dashboard-card preset-list">
          <button
            v-for="(preset, idx) in cfg.presets"
            :key="preset.__key"
            class="preset-row"
            :class="{ active: selectedPresetKey === preset.__key }"
            type="button"
            @click="selectPreset(preset.__key)"
          >
            <span class="preset-row-main">
              <span class="preset-row-title">{{ preset.name || tm('cards.unnamed') }}</span>
              <span class="preset-row-meta">{{ tm('cards.memberCount', { count: preset.members.length }) }}</span>
            </span>
            <v-chip size="x-small" variant="tonal" :color="preset.enabled ? 'success' : 'default'">
              {{ preset.enabled ? tm('cards.enabled') : tm('cards.disabled') }}
            </v-chip>
            <v-btn
              icon="mdi-content-copy"
              variant="text"
              density="comfortable"
              size="small"
              :aria-label="tm('actions.duplicate')"
              @click.stop="duplicatePreset(idx)"
            />
            <v-btn
              icon="mdi-delete-outline"
              variant="text"
              color="error"
              density="comfortable"
              size="small"
              :aria-label="tm('actions.delete')"
              @click.stop="removePreset(idx)"
            />
          </button>
        </aside>

        <section v-if="selectedPreset" class="dashboard-card dashboard-card--padded preset-editor">
          <div class="editor-section">
            <div class="editor-section-head">
              <div class="dashboard-section-title">{{ tm('section.presetSettings') }}</div>
              <v-switch
                v-model="selectedPreset.enabled"
                color="success"
                inset
                density="compact"
                hide-details
                :label="tm('form.enabled')"
              />
            </div>

            <div class="dashboard-form-grid dashboard-form-grid--single">
              <v-text-field
                v-model="selectedPreset.name"
                :label="tm('form.presetName')"
                :rules="[v => !!v || tm('messages.nameRequired'), v => namePattern.test(v) || tm('messages.namePattern')]"
                variant="outlined"
                density="comfortable"
                hide-details="auto"
              />
            </div>
          </div>

          <div class="editor-section">
            <div class="editor-section-head">
              <div class="dashboard-section-title">{{ tm('section.members') }}</div>
              <v-btn color="primary" variant="tonal" size="small" prepend-icon="mdi-plus" @click="addMember(selectedPreset)">
                {{ tm('actions.addMember') }}
              </v-btn>
            </div>

            <div class="member-list">
              <div v-for="(member, idx) in selectedPreset.members" :key="member.__key" class="member-row">
                <div class="member-row-top">
                  <v-switch
                    v-model="member.enabled"
                    color="success"
                    inset
                    density="compact"
                    hide-details
                  />
                  <v-text-field
                    v-model="member.name"
                    :label="tm('form.memberName')"
                    :rules="[v => !!v || tm('messages.nameRequired'), v => namePattern.test(v) || tm('messages.namePattern')]"
                    variant="outlined"
                    density="comfortable"
                    hide-details="auto"
                  />
                  <v-select
                    v-model="member.source_type"
                    :items="sourceTypeOptions"
                    :label="tm('form.memberSource')"
                    variant="outlined"
                    density="comfortable"
                    hide-details="auto"
                    @update:model-value="onMemberSourceChanged(member)"
                  />
                  <v-select
                    v-if="member.source_type === 'subagent'"
                    v-model="member.subagent_preset"
                    :items="availableSubAgentItems"
                    :label="tm('form.memberSubAgentPreset')"
                    variant="outlined"
                    density="comfortable"
                    hide-details="auto"
                    clearable
                  />
                  <div v-else class="selector-card member-persona-selector">
                    <PersonaSelector v-model="member.persona_id" />
                  </div>
                  <v-btn
                    icon="mdi-delete-outline"
                    variant="text"
                    color="error"
                    density="comfortable"
                    :aria-label="tm('actions.delete')"
                    @click="removeMember(selectedPreset, idx)"
                  />
                </div>
                <div v-if="member.source_type === 'subagent'" class="member-preview">
                  <template v-if="selectedSubAgentPreset(member)">
                    <div class="member-preview-title">{{ selectedSubAgentPreset(member)?.name }}</div>
                    <div class="member-preview-grid">
                      <span>{{ tm('preview.persona') }}</span>
                      <strong>{{ selectedSubAgentPreset(member)?.persona_id || tm('preview.systemPrompt') }}</strong>
                      <span>{{ tm('preview.provider') }}</span>
                      <strong>{{ selectedSubAgentPreset(member)?.provider_id || tm('preview.defaultProvider') }}</strong>
                      <span>{{ tm('preview.tools') }}</span>
                      <strong>{{ capabilitySummary(selectedSubAgentPreset(member)?.tools) }}</strong>
                      <span>{{ tm('preview.skills') }}</span>
                      <strong>{{ capabilitySummary(selectedSubAgentPreset(member)?.skills) }}</strong>
                    </div>
                  </template>
                  <div v-else class="member-preview-empty">{{ tm('preview.subAgentMissing') }}</div>
                </div>
                <div v-else class="member-preview member-preview--persona">
                  <PersonaQuickPreview :model-value="member.persona_id" />
                </div>
              </div>
            </div>
          </div>

          <div class="editor-section">
            <div class="dashboard-section-title">{{ tm('section.collaboration') }}</div>
            <div class="dashboard-form-grid">
              <v-select
                v-model="selectedPreset.initial_recipients"
                :items="enabledMemberNames"
                :label="tm('form.initialRecipients')"
                :hint="tm('form.initialRecipientsHint')"
                variant="outlined"
                density="comfortable"
                multiple
                chips
                closable-chips
                persistent-hint
                hide-details="auto"
              />
              <div class="field-with-note">
                <v-combobox
                  v-model="selectedPreset.principles"
                  :label="tm('form.principles')"
                  variant="outlined"
                  density="comfortable"
                  multiple
                  chips
                  closable-chips
                  hide-details="auto"
                />
                <v-tooltip location="top" max-width="420">
                  <template #activator="{ props }">
                    <span v-bind="props" class="field-note">
                      <v-icon size="14" icon="mdi-help-circle-outline" />
                      {{ tm('form.principlesNote') }}
                    </span>
                  </template>
                  <div class="field-note-tooltip">{{ tm('form.principlesExample') }}</div>
                </v-tooltip>
              </div>
              <v-select
                v-model="selectedPreset.summary_preset"
                :items="availableSubAgentItems"
                :label="tm('form.summaryPreset')"
                variant="outlined"
                density="comfortable"
                hide-details="auto"
                clearable
              />
              <div class="field-with-note collaboration-prompt">
                <v-textarea
                  v-model="selectedPreset.collaboration_prompt"
                  :label="tm('form.collaborationPrompt')"
                  variant="outlined"
                  density="comfortable"
                  auto-grow
                  hide-details="auto"
                />
                <v-tooltip location="top" max-width="460">
                  <template #activator="{ props }">
                    <span v-bind="props" class="field-note">
                      <v-icon size="14" icon="mdi-help-circle-outline" />
                      {{ tm('form.collaborationPromptNote') }}
                    </span>
                  </template>
                  <div class="field-note-tooltip">{{ tm('form.collaborationPromptExample') }}</div>
                </v-tooltip>
              </div>
              <div class="switch-line">
                <span>{{ tm('form.summaryIncludePrivate') }}</span>
                <v-switch
                  v-model="selectedPreset.summary_include_private"
                  color="primary"
                  inset
                  density="comfortable"
                  hide-details
                />
              </div>
            </div>
          </div>

          <div class="editor-section">
            <div class="dashboard-section-title">{{ tm('section.limits') }}</div>
            <div class="dashboard-form-grid">
              <v-text-field
                v-model.number="selectedPreset.token_limit"
                :label="tm('form.tokenLimit')"
                type="number"
                min="1"
                variant="outlined"
                density="comfortable"
                hide-details="auto"
              />
              <v-text-field
                v-model.number="selectedPreset.time_limit_seconds"
                :label="tm('form.timeLimitSeconds')"
                type="number"
                min="1"
                variant="outlined"
                density="comfortable"
                hide-details="auto"
              />
            </div>
          </div>
        </section>
      </div>

      <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000" location="top">
        {{ snackbar.message }}
        <template #actions>
          <v-btn variant="text" @click="snackbar.show = false">{{ tm('actions.close') }}</v-btn>
        </template>
      </v-snackbar>
    </v-container>
  </div>
</template>

<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useTheme } from 'vuetify'
import PersonaQuickPreview from '@/components/shared/PersonaQuickPreview.vue'
import PersonaSelector from '@/components/shared/PersonaSelector.vue'
import { useModuleI18n } from '@/i18n/composables'
import { askForConfirmation, useConfirmDialog } from '@/utils/confirmDialog'

type AgentGroupMember = {
  [key: string]: any
  __key: string
  name: string
  source_type: 'subagent' | 'persona'
  subagent_preset: string
  persona_id: string
  enabled: boolean
}

type AgentGroupPreset = {
  [key: string]: any
  __key: string
  name: string
  enabled: boolean
  members: AgentGroupMember[]
  initial_recipients: string[]
  principles: string[]
  collaboration_prompt: string
  summary_preset: string
  summary_include_private: boolean
  token_limit: number | null
  time_limit_seconds: number | null
}

type AgentGroupConfig = {
  [key: string]: any
  presets: AgentGroupPreset[]
}

type SubAgentPreset = {
  [key: string]: any
  name: string
  public_description?: string
  runtime_mode?: string
  persona_id?: string | null
  provider_id?: string | null
  tools?: string[] | null
  skills?: string[] | null
}

const { tm } = useModuleI18n('features/agent-group')
const theme = useTheme()
const confirmDialog = useConfirmDialog()
const namePattern = /^[a-z][a-z0-9_]{0,63}$/

const loading = ref(false)
const saving = ref(false)
const isDark = computed(() => theme.global.current.value.dark)
const selectedPresetKey = ref('')
const initialSnapshot = ref('')
const hasLoaded = ref(false)
const legacyWorkspaceKey = ['workspace', 'id'].join('_')
const availableSubAgentPresets = ref<SubAgentPreset[]>([])

const availableSubAgentItems = computed(() =>
  availableSubAgentPresets.value.map((agent) => ({
    title: agent.public_description ? `${agent.name} - ${agent.public_description}` : agent.name,
    value: agent.name
  }))
)

const sourceTypeOptions = computed(() => [
  { title: tm('form.memberSourceSubAgent'), value: 'subagent' },
  { title: tm('form.memberSourcePersona'), value: 'persona' }
])

const snackbar = ref({
  show: false,
  message: '',
  color: 'success'
})

const cfg = ref<AgentGroupConfig>({
  presets: []
})

const selectedPreset = computed(() =>
  cfg.value.presets.find((preset) => preset.__key === selectedPresetKey.value) ?? cfg.value.presets[0]
)

const enabledMemberNames = computed(() =>
  (selectedPreset.value?.members ?? [])
    .filter((member) => member.enabled && member.name)
    .map((member) => member.name)
)

const hasUnsavedChanges = computed(() => hasLoaded.value && serializeConfig(cfg.value) !== initialSnapshot.value)

function toast(message: string, color: 'success' | 'error' | 'warning' = 'success') {
  snackbar.value = { show: true, message, color }
}

function makeKey(): string {
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`
}

function toStringArray(value: any): string[] {
  return Array.isArray(value) ? value.map((item) => item.toString()) : []
}

function toCapabilityList(value: any): string[] | null {
  if (value === null) return null
  return toStringArray(value)
}

function withoutLegacyPresetFields(raw: any): Record<string, any> {
  const copy = { ...(raw ?? {}) }
  delete copy[legacyWorkspaceKey]
  return copy
}

function withoutLegacyMemberFields(raw: any): Record<string, any> {
  const copy = { ...(raw ?? {}) }
  for (const key of ['role', 'tools', 'skills']) {
    delete copy[key]
  }
  return copy
}

function toNullableNumber(value: any): number | null {
  if (value === null || value === undefined || value === '') return null
  const numberValue = Number(value)
  return Number.isFinite(numberValue) && numberValue > 0 ? numberValue : null
}

function normalizeMember(raw: any, index: number): AgentGroupMember {
  const sourceType = raw?.source_type === 'persona' || raw?.source_type === 'subagent'
    ? raw.source_type
    : raw?.persona_id && !raw?.subagent_preset
      ? 'persona'
      : 'subagent'
  const cleaned = withoutLegacyMemberFields(raw)
  return {
    ...cleaned,
    __key: raw?.__key ?? `${makeKey()}_${index}`,
    name: (raw?.name ?? '').toString(),
    source_type: sourceType,
    subagent_preset: sourceType === 'subagent' ? (raw?.subagent_preset ?? '').toString() : '',
    persona_id: sourceType === 'persona' ? (raw?.persona_id ?? '').toString() : '',
    enabled: raw?.enabled !== false
  }
}

function normalizePreset(raw: any, index: number): AgentGroupPreset {
  const membersRaw = Array.isArray(raw?.members) ? raw.members : []
  const cleaned = withoutLegacyPresetFields(raw)
  return {
    ...cleaned,
    __key: raw?.__key ?? `${makeKey()}_${index}`,
    name: (raw?.name ?? '').toString(),
    enabled: raw?.enabled !== false,
    members: membersRaw.map(normalizeMember),
    initial_recipients: toStringArray(raw?.initial_recipients),
    principles: toStringArray(raw?.principles),
    collaboration_prompt: (raw?.collaboration_prompt ?? '').toString(),
    summary_preset: (raw?.summary_preset ?? 'agent_group_summary').toString(),
    summary_include_private: !!raw?.summary_include_private,
    token_limit: toNullableNumber(raw?.token_limit),
    time_limit_seconds: toNullableNumber(raw?.time_limit_seconds)
  }
}

function normalizeConfig(raw: any): AgentGroupConfig {
  const presetsRaw = Array.isArray(raw?.presets) ? raw.presets : []
  return {
    ...raw,
    presets: presetsRaw.map(normalizePreset)
  }
}

function toSerializableConfig(config: AgentGroupConfig) {
  const { presets, ...topLevelConfig } = config
  return {
    ...topLevelConfig,
    presets: presets.map((preset) => {
      const { __key, members, ...presetConfig } = preset
      const cleanedPresetConfig = withoutLegacyPresetFields(presetConfig)
      return {
        ...cleanedPresetConfig,
        name: preset.name.trim(),
        enabled: preset.enabled,
        members: members.map((member) => {
          const { __key, ...memberConfig } = member
          const cleanedMemberConfig = withoutLegacyMemberFields(memberConfig)
          return {
            ...cleanedMemberConfig,
            name: member.name.trim(),
            source_type: member.source_type,
            subagent_preset: member.source_type === 'subagent' ? member.subagent_preset : '',
            persona_id: member.source_type === 'persona' ? member.persona_id : '',
            enabled: member.enabled,
          }
        }),
        initial_recipients: preset.initial_recipients ?? [],
        principles: preset.principles ?? [],
        collaboration_prompt: preset.collaboration_prompt,
        summary_preset: preset.summary_preset || 'agent_group_summary',
        summary_include_private: preset.summary_include_private,
        token_limit: toNullableNumber(preset.token_limit),
        time_limit_seconds: toNullableNumber(preset.time_limit_seconds)
      }
    })
  }
}

function serializeConfig(config: AgentGroupConfig): string {
  return JSON.stringify(toSerializableConfig(config))
}

async function loadSubAgentPresets() {
  try {
    const res = await axios.get('/api/subagent/config')
    if (res.data.status === 'ok' && Array.isArray(res.data.data?.agents)) {
      availableSubAgentPresets.value = res.data.data.agents
        .filter((agent: any) => agent?.name && agent?.runtime_mode === 'persistent')
        .map((agent: any) => ({
          ...agent,
          name: agent.name.toString(),
          public_description: agent.public_description?.toString(),
          persona_id: agent.persona_id ?? '',
          provider_id: agent.provider_id ?? null,
          tools: toCapabilityList(agent.tools),
          skills: toCapabilityList(agent.skills)
        }))
    }
  } catch {
    availableSubAgentPresets.value = []
  }
}

function selectedSubAgentPreset(member: AgentGroupMember): SubAgentPreset | undefined {
  return availableSubAgentPresets.value.find((agent) => agent.name === member.subagent_preset)
}

function capabilitySummary(value: string[] | null | undefined): string {
  if (value === null) return tm('preview.all')
  if (!value?.length) return tm('preview.none')
  return value.join(', ')
}

function onMemberSourceChanged(member: AgentGroupMember) {
  if (member.source_type === 'persona') {
    member.subagent_preset = ''
    return
  }
  member.persona_id = ''
}

async function loadConfig() {
  loading.value = true
  try {
    const res = await axios.get('/api/agent-group/config')
    if (res.data.status === 'ok') {
      cfg.value = normalizeConfig(res.data.data)
      selectedPresetKey.value = cfg.value.presets[0]?.__key ?? ''
      initialSnapshot.value = serializeConfig(cfg.value)
      hasLoaded.value = true
    } else {
      toast(res.data.message || tm('messages.loadConfigFailed'), 'error')
    }
  } catch (e: any) {
    toast(e?.response?.data?.message || tm('messages.loadConfigFailed'), 'error')
  } finally {
    loading.value = false
  }
}

function addPreset() {
  const preset: AgentGroupPreset = {
    __key: makeKey(),
    name: '',
    enabled: true,
    members: [],
    initial_recipients: [],
    principles: [],
    collaboration_prompt: '',
    summary_preset: 'agent_group_summary',
    summary_include_private: false,
    token_limit: null,
    time_limit_seconds: null
  }
  cfg.value.presets.push(preset)
  selectedPresetKey.value = preset.__key
}

function duplicatePreset(index: number) {
  const source = cfg.value.presets[index]
  if (!source) return
  const copy = normalizePreset(toSerializableConfig({ presets: [source] }).presets[0], index + 1)
  copy.__key = makeKey()
  copy.name = source.name ? `${source.name}_copy` : ''
  copy.members = copy.members.map((member) => ({ ...member, __key: makeKey() }))
  cfg.value.presets.splice(index + 1, 0, copy)
  selectedPresetKey.value = copy.__key
}

function removePreset(index: number) {
  const removed = cfg.value.presets.splice(index, 1)[0]
  if (removed?.__key === selectedPresetKey.value) {
    selectedPresetKey.value = cfg.value.presets[Math.min(index, cfg.value.presets.length - 1)]?.__key ?? ''
  }
}

function selectPreset(key: string) {
  selectedPresetKey.value = key
}

function addMember(preset: AgentGroupPreset) {
  preset.members.push({
    __key: makeKey(),
    name: '',
    source_type: 'subagent',
    subagent_preset: '',
    persona_id: '',
    enabled: true
  })
}

function removeMember(preset: AgentGroupPreset, index: number) {
  const removed = preset.members.splice(index, 1)[0]
  if (removed) {
    preset.initial_recipients = preset.initial_recipients.filter((name) => name !== removed.name)
  }
}

function validateBeforeSave(): boolean {
  const presetNames = new Set<string>()

  for (const preset of cfg.value.presets) {
    const presetName = preset.name.trim()
    if (!presetName) {
      toast(tm('messages.presetNameMissing'), 'warning')
      return false
    }
    if (!namePattern.test(presetName)) {
      toast(tm('messages.presetNameInvalid'), 'warning')
      return false
    }
    if (presetNames.has(presetName)) {
      toast(tm('messages.presetNameDuplicate', { name: presetName }), 'warning')
      return false
    }
    presetNames.add(presetName)

    const memberNames = new Set<string>()
    for (const member of preset.members) {
      const memberName = member.name.trim()
      if (!memberName) {
        toast(tm('messages.memberNameMissing', { preset: presetName }), 'warning')
        return false
      }
      if (!namePattern.test(memberName)) {
        toast(tm('messages.memberNameInvalid', { preset: presetName }), 'warning')
        return false
      }
      if (memberNames.has(memberName)) {
        toast(tm('messages.memberNameDuplicate', { preset: presetName, name: memberName }), 'warning')
        return false
      }
      memberNames.add(memberName)
      if (member.source_type === 'subagent' && !member.subagent_preset) {
        toast(tm('messages.memberPresetMissing', { preset: presetName, member: memberName }), 'warning')
        return false
      }
      if (member.source_type === 'persona' && !member.persona_id) {
        toast(tm('messages.memberPersonaMissing', { preset: presetName, member: memberName }), 'warning')
        return false
      }
    }

    const enabledMembers = new Set(preset.members.filter((member) => member.enabled).map((member) => member.name))
    for (const recipient of preset.initial_recipients) {
      if (!enabledMembers.has(recipient)) {
        toast(tm('messages.initialRecipientInvalid', { preset: presetName, name: recipient }), 'warning')
        return false
      }
    }
  }

  return true
}

async function save() {
  if (!validateBeforeSave()) return
  saving.value = true
  try {
    const payload = toSerializableConfig(cfg.value)
    const res = await axios.post('/api/agent-group/config', payload)
    if (res.data.status === 'ok') {
      initialSnapshot.value = serializeConfig(cfg.value)
      hasLoaded.value = true
      toast(res.data.message || tm('messages.saveSuccess'), 'success')
    } else {
      toast(res.data.message || tm('messages.saveFailed'), 'error')
    }
  } catch (e: any) {
    toast(e?.response?.data?.message || tm('messages.saveFailed'), 'error')
  } finally {
    saving.value = false
  }
}

async function reload() {
  if (hasUnsavedChanges.value) {
    const confirmed = await askForConfirmation(
      tm('messages.unsavedChangesReloadConfirm'),
      confirmDialog
    )
    if (!confirmed) return
  }
  await Promise.all([loadConfig(), loadSubAgentPresets()])
}

async function confirmLeaveIfNeeded(): Promise<boolean> {
  if (!hasUnsavedChanges.value) return true

  return askForConfirmation(
    tm('messages.unsavedChangesLeaveConfirm'),
    confirmDialog
  )
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!hasUnsavedChanges.value) return

  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  reload()
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
})

onBeforeRouteLeave(async () => {
  return await confirmLeaveIfNeeded()
})
</script>

<style scoped>
@import '@/styles/dashboard-shell.css';

.agent-group-page {
  padding-bottom: 40px;
}

.unsaved-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 18px;
  border: 1px solid rgba(var(--v-theme-warning), 0.22);
  border-radius: 8px;
  background: rgba(var(--v-theme-warning), 0.08);
  color: var(--dashboard-text);
  font-size: 13px;
  line-height: 1.5;
}

.empty-card {
  min-height: 280px;
}

.empty-wrap {
  min-height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--dashboard-muted);
}

.empty-title {
  font-size: 20px;
  font-weight: 650;
  color: var(--dashboard-text);
  margin-bottom: 8px;
}

.preset-layout {
  display: grid;
  grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.preset-list {
  display: grid;
  gap: 4px;
  padding: 8px;
}

.preset-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--dashboard-text);
  text-align: left;
  cursor: pointer;
}

.preset-row:hover,
.preset-row.active {
  border-color: rgba(var(--v-theme-primary), 0.24);
  background: rgba(var(--v-theme-primary), 0.08);
}

.preset-row-main {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.preset-row-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 15px;
  font-weight: 650;
}

.preset-row-meta {
  color: var(--dashboard-muted);
  font-size: 12px;
}

.preset-editor {
  display: grid;
  gap: 22px;
}

.editor-section {
  display: grid;
  gap: 14px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--dashboard-border);
}

.editor-section:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.editor-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.member-list {
  display: grid;
  gap: 14px;
}

.member-row {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--dashboard-border);
  border-radius: 8px;
  background: rgba(var(--v-theme-primary), 0.02);
}

.member-row-top {
  display: grid;
  grid-template-columns: auto minmax(140px, 1fr) minmax(140px, 0.7fr) minmax(220px, 1.2fr) auto;
  gap: 10px;
  align-items: start;
}

.member-persona-selector {
  min-height: 56px;
}

.member-preview {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--dashboard-border);
  border-radius: 8px;
  background: rgba(var(--v-theme-surface), 0.38);
}

.member-preview-title {
  font-size: 13px;
  font-weight: 650;
  color: var(--dashboard-text);
}

.member-preview-grid {
  display: grid;
  grid-template-columns: repeat(4, auto minmax(0, 1fr));
  gap: 6px 10px;
  align-items: baseline;
  font-size: 12px;
}

.member-preview-grid span,
.member-preview-empty {
  color: var(--dashboard-muted);
}

.member-preview-grid strong {
  min-width: 0;
  overflow: hidden;
  color: var(--dashboard-text);
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.member-preview--persona {
  padding: 0;
  overflow: hidden;
}

.collaboration-prompt {
  grid-column: 1 / -1;
}

.field-with-note {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.field-note {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  width: fit-content;
  color: var(--dashboard-muted);
  cursor: help;
  font-size: 12px;
  line-height: 1.45;
}

.field-note-tooltip {
  max-width: 420px;
  white-space: normal;
  line-height: 1.5;
}

.switch-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 56px;
  padding: 0 12px;
  border: 1px solid var(--dashboard-border);
  border-radius: 8px;
  color: var(--dashboard-text);
  font-size: 14px;
}

@media (max-width: 1180px) {
  .preset-layout {
    grid-template-columns: 1fr;
  }

  .preset-list {
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  }
}

@media (max-width: 900px) {
  .member-row-top,
  .member-preview-grid {
    grid-template-columns: 1fr;
  }

  .editor-section-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

<template>
  <div class="px-4 h-100 d-flex flex-column">
    <div class="page-title d-flex align-center">
      {{ $t('IchM11Page.title') }}
    </div>
    <div v-if="studiesGeneralStore.selectedStudy?.uid" class="mb-4">
      <v-btn
        :loading="authoringLoading"
        :disabled="saving"
        @click="loadAuthoring"
        >{{ $t('IchM11Page.author') }}</v-btn
      >
      <v-btn
        class="ml-2"
        :disabled="!previewHtml || loading"
        @click="downloadPreview"
        >{{ $t('IchM11Page.download') }}</v-btn
      >
    </div>
    <v-alert v-if="authoringError" type="error" role="alert" class="mb-4">{{
      authoringError
    }}</v-alert>
    <v-card v-if="authoring" class="pa-4 mb-4">
      <p>{{ $t('IchM11Page.authoring_help') }}</p>
      <p>{{ authoring.assessment?.meaning }}</p>
      <v-btn
        :disabled="saving || authoringLoading"
        @click="loadAuthoring(true)"
        >{{ $t('IchM11Page.reload_source') }}</v-btn
      >
      <details v-if="recoverySource">
        <summary>{{ $t('IchM11Page.recovery_source') }}</summary>
        <pre>{{ recoverySource }}</pre>
      </details>
      <v-select
        v-model="documentId"
        :items="documentOptions"
        item-title="name"
        item-value="id"
        :label="$t('IchM11Page.document')"
        clearable
      />
      <details>
        <summary>{{ $t('IchM11Page.coverage') }}</summary>
        <div
          v-for="document in authoring.assessment?.documents || []"
          :key="document.document_id"
        >
          <strong
            >{{ document.name }} —
            {{
              document.section_complete
                ? $t('IchM11Page.covered')
                : $t('IchM11Page.incomplete')
            }}</strong
          >
          <ul>
            <li v-for="(issue, index) in document.issues" :key="index">
              {{ issue.section_number }} {{ issue.title }}
            </li>
          </ul>
        </div>
        <p v-if="!authoring.assessment">
          {{ $t('IchM11Page.no_authored_content') }}
        </p>
        <pre>{{ JSON.stringify(authoring.required_sections, null, 2) }}</pre>
      </details>
      <v-textarea
        v-model="sourceJson"
        :label="$t('IchM11Page.source')"
        rows="12"
        max-rows="18"
        auto-grow
        :readonly="historical || saving || authoringLoading"
        spellcheck="false"
      />
      <v-text-field
        v-model="changeReason"
        :label="$t('IchM11Page.reason')"
        :readonly="historical || saving || authoringLoading"
      />
      <v-alert v-if="historical" type="info">{{
        $t('IchM11Page.historical')
      }}</v-alert>
      <v-btn
        v-else
        color="primary"
        :loading="saving"
        :disabled="!changeReason.trim() || authoringLoading"
        @click="saveAuthoring"
        >{{ $t('IchM11Page.save') }}</v-btn
      >
      <p v-if="saveSuccess" role="status">{{ $t('IchM11Page.saved') }}</p>
    </v-card>
    <v-card class="pa-6 flex-grow-1 d-flex flex-column" :aria-busy="loading">
      <div v-if="loading" class="text-center py-12" role="status">
        <v-progress-circular size="64" color="primary" indeterminate />
        <p class="mt-4">{{ $t('IchM11Page.loading') }}</p>
      </div>
      <v-alert v-else-if="loadError" type="error" role="alert">
        <p>{{ $t('IchM11Page.load_error') }}</p>
        <v-btn class="mt-4" @click="retryCount++">
          {{ $t('IchM11Page.retry') }}
        </v-btn>
      </v-alert>
      <v-alert
        v-else-if="!studiesGeneralStore.selectedStudy?.uid"
        type="info"
        role="status"
      >
        {{ $t('_global.no_study_selected') }}
      </v-alert>
      <iframe
        v-else-if="previewHtml"
        class="frame flex-grow-1"
        :title="$t('IchM11Page.title')"
        :srcdoc="previewHtml"
        sandbox="allow-scripts"
      />
      <v-alert v-else type="info" role="status">
        {{ $t('IchM11Page.empty') }}
      </v-alert>
    </v-card>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import study from '@/api/study'

const studiesGeneralStore = useStudiesGeneralStore()

const loading = ref(false)
const loadError = ref(false)
const previewHtml = ref('')
const retryCount = ref(0)
const documentId = ref(null)
const authoring = ref(null)
const sourceJson = ref('')
const changeReason = ref('')
const authoringLoading = ref(false)
const authoringError = ref('')
const saving = ref(false)
const saveSuccess = ref(false)
const recoverySource = ref('')
const drafts = new Map()
let selectionRevision = 0
const scopeKey = computed(() =>
  JSON.stringify([
    studiesGeneralStore.selectedStudy?.uid,
    studiesGeneralStore.selectedStudyVersion,
  ])
)
const historical = computed(
  () =>
    Boolean(studiesGeneralStore.selectedStudyVersion) ||
    authoring.value?.study_status === 'LOCKED'
)
const documentOptions = computed(
  () => authoring.value?.content?.documents || []
)

watch(scopeKey, (_next, previous) => {
  selectionRevision++
  if (authoring.value)
    drafts.set(previous, {
      authoring: authoring.value,
      sourceJson: sourceJson.value,
      reason: changeReason.value,
    })
  authoring.value = null
  sourceJson.value = ''
  changeReason.value = ''
  documentId.value = null
  recoverySource.value = ''
  authoringError.value = ''
  saveSuccess.value = false
  authoringLoading.value = false
  saving.value = false
})

function describeError(error) {
  const data = error.response?.data
  return (
    (Array.isArray(data?.details)
      ? data.details.map((item) => item.msg).join('\n')
      : '') ||
    data?.message ||
    (typeof data?.detail === 'string'
      ? data.detail
      : data?.detail
        ? JSON.stringify(data.detail)
        : error.message)
  )
}

async function loadAuthoring(reload = false) {
  // DOM click events are not an instruction to discard an open editor.
  reload = reload === true
  if (authoring.value && !reload) return
  if (reload) recoverySource.value = sourceJson.value
  const key = scopeKey.value
  const revision = selectionRevision
  const draft = drafts.get(key)
  if (draft && !reload) {
    authoring.value = draft.authoring
    sourceJson.value = draft.sourceJson
    changeReason.value = draft.reason
    drafts.delete(key)
    return
  }
  authoringLoading.value = true
  authoringError.value = ''
  try {
    const { data } = await study.getProtocolDocuments(
      studiesGeneralStore.selectedStudy.uid,
      studiesGeneralStore.selectedStudyVersion
    )
    if (scopeKey.value !== key || selectionRevision !== revision) return
    authoring.value = data
    sourceJson.value = JSON.stringify(
      data.content || {
        documents: [],
        narrative_content_items: [],
        organizations: [],
        bindings: [],
        section_dispositions: [],
        synthetic: false,
      },
      null,
      2
    )
    saveSuccess.value = false
  } catch (error) {
    if (scopeKey.value === key && selectionRevision === revision)
      authoringError.value = describeError(error)
  } finally {
    if (scopeKey.value === key && selectionRevision === revision)
      authoringLoading.value = false
  }
}

async function saveAuthoring() {
  if (historical.value || !authoring.value || saving.value) return
  const key = scopeKey.value
  const revision = selectionRevision
  saving.value = true
  authoringError.value = ''
  saveSuccess.value = false
  try {
    const { data } = await study.saveProtocolDocuments(
      studiesGeneralStore.selectedStudy.uid,
      {
        expected_content_hash: authoring.value.content_hash,
        expected_study_version: authoring.value.study_value_version,
        reason: changeReason.value,
        content: JSON.parse(sourceJson.value),
      }
    )
    if (scopeKey.value !== key || selectionRevision !== revision) return
    authoring.value = data
    sourceJson.value = JSON.stringify(data.content, null, 2)
    saveSuccess.value = true
    retryCount.value++
  } catch (error) {
    if (scopeKey.value === key && selectionRevision === revision)
      authoringError.value = describeError(error)
  } finally {
    if (scopeKey.value === key && selectionRevision === revision)
      saving.value = false
  }
}

function downloadPreview() {
  if (!previewHtml.value || loading.value) return
  const url = URL.createObjectURL(
    new Blob([previewHtml.value], { type: 'text/html;charset=utf-8' })
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'protocol-draft.html'
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

watch(
  [
    () => studiesGeneralStore.selectedStudy?.uid,
    () => studiesGeneralStore.selectedStudyVersion,
    retryCount,
    documentId,
  ],
  async (
    [studyUid, studyVersion, _retry, selectedDocument],
    _previous,
    onCleanup
  ) => {
    const controller = new AbortController()
    let current = true
    // Cleanup runs on a selection change, retry, or component unmount. The
    // current flag also protects against a transport that completes after abort.
    onCleanup(() => {
      current = false
      controller.abort()
    })

    previewHtml.value = ''
    loadError.value = false
    loading.value = Boolean(studyUid)
    if (!studyUid) return

    try {
      const response = await study.getDdfIchM11(studyUid, studyVersion, {
        signal: controller.signal,
        documentId: selectedDocument,
      })
      if (!current) return
      if (typeof response.data !== 'string') {
        loadError.value = true
      } else {
        previewHtml.value = response.data.trim()
      }
    } catch {
      if (current) loadError.value = true
    } finally {
      if (current) loading.value = false
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.frame {
  width: 100%;
  min-height: 32rem;
  border: 0;
}
</style>

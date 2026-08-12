<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('EdcExport.title') }} ({{ studyId }})
    </div>

    <v-card class="mb-4" elevation="1">
      <v-card-title>{{ $t('EdcExport.preview_title') }}</v-card-title>
      <v-card-text>
        <p class="mb-4">{{ $t('EdcExport.preview_help') }}</p>
        <v-btn
          color="primary"
          :loading="loadingBundle"
          data-cy="edc-preview-bundle"
          @click="loadBundle"
        >
          {{ $t('EdcExport.preview_action') }}
        </v-btn>
        <v-btn
          v-if="bundle"
          class="ml-4"
          variant="outlined"
          data-cy="edc-download-bundle"
          @click="downloadBundle"
        >
          {{ $t('EdcExport.download_action') }}
        </v-btn>

        <template v-if="bundle">
          <v-table class="mt-6" density="compact">
            <tbody>
              <tr>
                <td>{{ $t('EdcExport.study_name') }}</td>
                <td>{{ bundle.study.name }}</td>
              </tr>
              <tr>
                <td>{{ $t('EdcExport.visits') }}</td>
                <td>{{ bundle.visits.length }}</td>
              </tr>
              <tr>
                <td>{{ $t('EdcExport.forms') }}</td>
                <td>{{ bundle.forms.forms.length }}</td>
              </tr>
              <tr>
                <td>{{ $t('EdcExport.assignments') }}</td>
                <td>{{ bundle.visitFormAssignments.length }}</td>
              </tr>
            </tbody>
          </v-table>

          <v-alert
            v-if="censusRows.length === 0"
            type="success"
            class="mt-4"
            :text="$t('EdcExport.census_clean')"
          />
          <v-alert v-else type="warning" class="mt-4">
            <div class="mb-2">
              {{ $t('EdcExport.census_warnings', { count: censusRows.length }) }}
            </div>
            <ul>
              <li v-for="(row, index) in censusRows" :key="index">
                <strong>{{ row.kind }}</strong> — {{ row.ref }}: {{ row.detail }}
              </li>
            </ul>
          </v-alert>
        </template>
      </v-card-text>
    </v-card>

    <v-card elevation="1">
      <v-card-title>{{ $t('EdcExport.send_title') }}</v-card-title>
      <v-card-text>
        <p class="mb-4">{{ $t('EdcExport.send_help') }}</p>
        <v-btn
          color="secondary"
          :loading="sendingDryRun"
          data-cy="edc-dry-run"
          @click="send(true)"
        >
          {{ $t('EdcExport.dry_run_action') }}
        </v-btn>
        <v-btn
          color="primary"
          class="ml-4"
          :loading="sendingReal"
          :disabled="!dryRunSucceeded"
          data-cy="edc-send"
          @click="send(false)"
        >
          {{ $t('EdcExport.send_action') }}
        </v-btn>

        <v-alert v-if="sendError" type="error" class="mt-4" :text="sendError" />
        <template v-if="sendResult">
          <v-alert
            :type="sendResult.statusCode < 300 ? 'success' : 'error'"
            class="mt-4"
          >
            <div>
              {{
                sendResult.dryRun
                  ? $t('EdcExport.dry_run_result', {
                      status: sendResult.statusCode,
                    })
                  : $t('EdcExport.send_result', { status: sendResult.statusCode })
              }}
            </div>
            <div v-if="quarantineStudyId" class="mt-2">
              {{ $t('EdcExport.quarantine_notice', { id: quarantineStudyId }) }}
            </div>
          </v-alert>
          <v-expansion-panels class="mt-4">
            <v-expansion-panel :title="$t('EdcExport.edc_response')">
              <v-expansion-panel-text>
                <pre class="edc-response">{{
                  JSON.stringify(sendResult.edcResponse, null, 2)
                }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </template>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import edcExport from '@/api/edcExport'
import { useStudiesGeneralStore } from '@/stores/studies-general'

const studiesGeneralStore = useStudiesGeneralStore()
const studyId = computed(() => studiesGeneralStore.studyId)
const studyUid = computed(() => studiesGeneralStore.selectedStudy.uid)

const bundle = ref(null)
const loadingBundle = ref(false)
const sendingDryRun = ref(false)
const sendingReal = ref(false)
const sendResult = ref(null)
const sendError = ref(null)
const dryRunSucceeded = ref(false)

const censusRows = computed(() => bundle.value?._exportCensus?.rows ?? [])
const quarantineStudyId = computed(() => {
  const response = sendResult.value?.edcResponse
  return (
    response?.newStudyId ??
    response?.data?.newStudyId ??
    response?.studyId ??
    response?.data?.studyId ??
    null
  )
})

async function loadBundle() {
  loadingBundle.value = true
  try {
    const resp = await edcExport.getStudyBundle(studyUid.value)
    bundle.value = resp.data
  } finally {
    loadingBundle.value = false
  }
}

function downloadBundle() {
  const blob = new Blob([JSON.stringify(bundle.value, null, 2)], {
    type: 'application/json',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'study.ecrfstudy'
  link.click()
  URL.revokeObjectURL(url)
}

async function send(dryRun) {
  const loading = dryRun ? sendingDryRun : sendingReal
  loading.value = true
  sendError.value = null
  try {
    const resp = await edcExport.send(studyUid.value, dryRun)
    sendResult.value = resp.data
    if (dryRun && resp.data.statusCode < 300) {
      dryRunSucceeded.value = true
    }
  } catch (error) {
    sendError.value =
      error.response?.data?.message ?? error.message ?? String(error)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.edc-response {
  max-height: 400px;
  overflow: auto;
  font-size: 0.8rem;
}
</style>

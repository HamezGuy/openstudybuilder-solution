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
          :disabled="!studyUid"
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
        <v-alert
          v-if="bundleError"
          type="error"
          class="mt-4"
          :text="bundleError"
          role="alert"
        />

        <template v-if="bundle && review">
          <v-alert type="info" class="mt-4">
            USDM 4.0.0 draft for the selected study version. Mapping completeness,
            clinical approval and EDC activation have separate review steps.
          </v-alert>
          <v-table class="mt-6" density="compact">
            <tbody>
              <tr>
                <td>{{ $t('EdcExport.study_name') }}</td>
                <td>{{ review.name }}</td>
              </tr>
              <tr>
                <td>Selected study version</td>
                <td>{{ review.version ?? 'Selection required' }}</td>
              </tr>
              <tr>
                <td>Selected design</td>
                <td>{{ review.design ?? 'Selection required' }}</td>
              </tr>
              <tr>
                <td>{{ $t('EdcExport.visits') }}</td>
                <td>{{ review.counts.visits }}</td>
              </tr>
              <tr>
                <td>{{ $t('EdcExport.forms') }}</td>
                <td>{{ review.counts.forms }}</td>
              </tr>
              <tr>
                <td>Acquisition fields</td>
                <td>{{ review.counts.fields }}</td>
              </tr>
              <tr>
                <td>{{ $t('EdcExport.assignments') }}</td>
                <td>{{ review.counts.assignments }}</td>
              </tr>
            </tbody>
          </v-table>
          <v-alert
            v-if="!review.selectionComplete"
            type="warning"
            class="mt-4"
          >
            This document retains {{ review.counts.versions }} versions and
            {{ review.counts.designs }} designs. Select the intended version and
            design during EDC build review; the export has not selected the first.
          </v-alert>
          <v-alert
            v-if="review.mappingReport?.issues?.length"
            type="warning"
            class="mt-4"
            data-cy="edc-mapping-issues"
          >
            <p>Resolve these source facts before releasing the study:</p>
            <ul>
              <li
                v-for="(issue, index) in review.mappingReport.issues"
                :key="index"
              >
                <strong>{{ issue.code }}</strong>: {{ issue.message }}
                <div v-if="issue.sourcePath">Source: {{ issue.sourcePath }}</div>
                <div v-if="issue.targetPath">Study field: {{ issue.targetPath }}</div>
                <div v-if="issue.resolution">{{ issue.resolution }}</div>
              </li>
            </ul>
          </v-alert>
          <v-expansion-panels class="mt-4">
            <v-expansion-panel title="Complete canonical study content">
              <v-expansion-panel-text>
                <p>
                  All versions and designs are retained in the download. These
                  counts describe canonical content, not installed EDC behavior.
                </p>
                <v-table density="compact">
                  <thead>
                    <tr><th scope="col">Study element</th><th scope="col">Count</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="entity in review.entities" :key="entity.name">
                      <td>{{ entity.name }}</td><td>{{ entity.count }}</td>
                    </tr>
                  </tbody>
                </v-table>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>

          <v-alert
            v-if="censusRows.length === 0"
            type="info"
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
          :disabled="!review || review.authority?.mode !== 'legacy'"
          data-cy="edc-dry-run"
          @click="send(true)"
        >
          {{ $t('EdcExport.dry_run_action') }}
        </v-btn>
        <p class="mt-4">
          This endpoint supports a comparison dry run in an explicitly configured
          migration environment. Release and deployment use the native study
          approval workflow.
        </p>

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
import { computed, ref, watch } from 'vue'
import edcExport from '@/api/edcExport'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import { edcStudyReview } from '@/utils/edcStudyReview'

const studiesGeneralStore = useStudiesGeneralStore()
const studyId = computed(() => studiesGeneralStore.studyId)
const studyUid = computed(() => studiesGeneralStore.selectedStudy?.uid)
const studyVersion = computed(() => studiesGeneralStore.selectedStudyVersion)

const bundle = ref(null)
const review = ref(null)
const bundleError = ref(null)
const loadingBundle = ref(false)
const sendingDryRun = ref(false)
const sendResult = ref(null)
const sendError = ref(null)
let requestGeneration = 0

const censusRows = computed(() => review.value?.censusRows ?? [])
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

watch([studyUid, studyVersion], () => {
  requestGeneration++
  bundle.value = null
  review.value = null
  bundleError.value = null
  sendResult.value = null
  sendError.value = null
  loadingBundle.value = false
  sendingDryRun.value = false
})

async function loadBundle() {
  const generation = ++requestGeneration
  loadingBundle.value = true
  bundle.value = null
  review.value = null
  bundleError.value = null
  sendResult.value = null
  sendError.value = null
  try {
    const resp = await edcExport.getStudyBundle(studyUid.value, studyVersion.value)
    const projection = edcStudyReview(resp.data)
    if (generation !== requestGeneration) return
    bundle.value = resp.data
    review.value = projection
  } catch (error) {
    if (generation === requestGeneration) {
      bundleError.value =
        error.response?.data?.message ?? error.message ?? String(error)
    }
  } finally {
    if (generation === requestGeneration) loadingBundle.value = false
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
  if (!dryRun || !review.value) return
  const generation = requestGeneration
  sendingDryRun.value = true
  sendError.value = null
  try {
    const resp = await edcExport.send(
      studyUid.value,
      true,
      studyVersion.value
    )
    if (generation !== requestGeneration) return
    sendResult.value = resp.data
  } catch (error) {
    if (generation === requestGeneration) {
      sendError.value =
        error.response?.data?.message ?? error.message ?? String(error)
    }
  } finally {
    if (generation === requestGeneration) sendingDryRun.value = false
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

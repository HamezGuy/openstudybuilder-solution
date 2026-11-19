<template>
  <div class="px-4">
    <h1 class="page-title">
      {{ $t('EdcExport.title') }}
      <span v-if="studyUid">({{ studyId || studyUid }})</span>
    </h1>
    <p v-if="!studyUid">Select a study to review its EDC export.</p>
    <template v-else>
      <v-card class="mb-4" elevation="1">
        <v-card-title>Release snapshot</v-card-title>
        <v-card-text>
          <p class="mb-3">{{ versionLabel }}</p>
          <v-progress-linear
            v-if="!review"
            indeterminate
            aria-label="Reading release snapshot"
          />
          <template v-else>
            <v-alert
              :type="presentation.type"
              :title="presentation.title"
              role="status"
              data-cy="edc-release-state"
            >
              {{ presentation.detail }}
            </v-alert>
            <ul v-if="review.blockers?.length" class="ml-5 mt-3">
              <li
                v-for="(blocker, index) in review.blockers"
                :key="index"
                class="mb-2"
              >
                <strong>{{ blocker.code }}</strong>
                <div v-if="blocker.path">{{ blocker.path }}</div>
                <div>{{ blocker.detail }}</div>
              </li>
            </ul>
            <div v-if="review.hash" class="mt-3 text-wrap">
              Snapshot SHA-256
              <code class="snapshot-hash">{{ review.hash }}</code>
            </div>
          </template>
          <v-btn
            class="mt-4"
            variant="outlined"
            :disabled="!review"
            @click="refresh++"
            >Refresh review</v-btn
          >
          <v-btn
            v-if="snapshot"
            class="mt-4 ml-2"
            variant="outlined"
            @click="downloadSnapshot"
          >
            Download review snapshot
          </v-btn>
        </v-card-text>
      </v-card>
      <v-card
        v-if="review && !['denied', 'signed-out'].includes(review.state)"
        class="mb-4"
        elevation="1"
      >
        <v-card-title>Study design in OpenStudyBuilder</v-card-title>
        <v-card-text>
          <p>
            These counts describe the study design at the selected version. EDC
            release requires a separate approval.
          </p>
          <v-table density="compact" class="mt-3" data-cy="edc-working-counts">
            <thead>
              <tr>
                <th scope="col">Design record</th>
                <th scope="col">Count</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="resource in workingResources" :key="resource.key">
                <th scope="row">{{ resource.label }}</th>
                <td>{{ displayCount(resource.key) }}</td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>
      <EdcLegacyComparison
        v-if="review?.mode === 'legacy'"
        :key="studyUid + '/' + studyVersion"
      />
    </template>
  </div>
</template>

<script setup>
import { computed, ref, shallowRef, watch } from 'vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import edcExport from '@/api/edcExport'
import exportLoader from '@/utils/exportLoader'
import EdcLegacyComparison from '@/components/studies/EdcLegacyComparison.vue'
import {
  loadWorkingCounts,
  readFailure,
  reviewSnapshot,
  workingResources,
} from '@/utils/edcReleaseReview'

const store = useStudiesGeneralStore()
const studyUid = computed(() => store.selectedStudy?.uid)
const studyId = computed(() => store.studyId)
const studyVersion = computed(() => store.selectedStudyVersion)
const versionLabel = computed(() =>
  studyVersion.value == null || Number(studyVersion.value) === 0
    ? 'Current working version'
    : 'Selected study version ' + studyVersion.value
)
const refresh = ref(0)
const review = ref(null)
const snapshot = shallowRef(null)
const counts = ref({})

const presentations = {
  eligible: {
    type: 'success',
    title: 'Eligible for release review',
    detail:
      'OpenStudyBuilder reports that this version passed its release checks. The controlled approval and EDC deployment steps follow separately.',
  },
  held: {
    type: 'warning',
    title: 'Release held',
    detail:
      'Resolve the source requirements reported by OpenStudyBuilder before exporting this version.',
  },
  denied: {
    type: 'error',
    title: 'Access denied',
    detail:
      'Your account does not have permission to read this study snapshot.',
  },
  'signed-out': {
    type: 'warning',
    title: 'Sign in required',
    detail: 'Sign in again to read the selected study.',
  },
  unavailable: {
    type: 'error',
    title: 'Release snapshot unavailable',
    detail:
      'OpenStudyBuilder could not return a verified snapshot for this study and version. Refresh to retry.',
  },
}
const presentation = computed(() => presentations[review.value?.state])

function displayCount(key) {
  const count = review.value?.counts?.[key] ?? counts.value[key]?.count
  if (count != null) return count
  const state = counts.value[key]?.state
  return (
    {
      denied: 'Access denied',
      'signed-out': 'Sign in required',
      unavailable: 'Unavailable',
    }[state] ?? 'Loading…'
  )
}

function downloadSnapshot() {
  if (!snapshot.value) return
  exportLoader.downloadFile(
    JSON.stringify(snapshot.value, null, 2),
    'application/json',
    `OpenStudyBuilder-${snapshot.value.study_uid}-${snapshot.value.content_hash}.json`
  )
}

watch(
  [studyUid, studyVersion, refresh],
  async ([uid, version], previous, onCleanup) => {
    const controller = new AbortController()
    const { signal } = controller
    onCleanup(() => controller.abort())
    review.value = null
    snapshot.value = null
    counts.value = {}
    if (!uid) return
    let result
    try {
      const { data } = await edcExport.getAuthoritySnapshot(
        uid,
        version,
        signal
      )
      result = reviewSnapshot(data, uid, version)
      if (!signal.aborted) snapshot.value = data
    } catch (error) {
      result = readFailure(error)
    }
    if (signal.aborted) return
    review.value = result
    await loadWorkingCounts(
      edcExport,
      uid,
      version,
      result,
      signal,
      (key, value) => {
        counts.value[key] = value
      }
    )
  },
  { immediate: true }
)
</script>

<style scoped>
.snapshot-hash {
  overflow-wrap: anywhere;
}
</style>

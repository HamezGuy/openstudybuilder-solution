<template>
  <div class="px-4 h-100 d-flex flex-column">
    <div class="page-title d-flex align-center">
      {{ $t('IchM11Page.title') }}
    </div>
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
import { ref, watch } from 'vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import study from '@/api/study'

const studiesGeneralStore = useStudiesGeneralStore()

const loading = ref(false)
const loadError = ref(false)
const previewHtml = ref('')
const retryCount = ref(0)

watch(
  [
    () => studiesGeneralStore.selectedStudy?.uid,
    () => studiesGeneralStore.selectedStudyVersion,
    retryCount,
  ],
  async ([studyUid, studyVersion], _previous, onCleanup) => {
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

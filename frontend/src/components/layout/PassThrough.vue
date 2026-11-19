<template>
  <router-view v-if="canRender" :key="pageKey" />
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useStudiesGeneralStore } from '@/stores/studies-general'

const route = useRoute()
const studies = useStudiesGeneralStore()
// M11 handles scope changes itself to retain unsaved per-version document edits.
const managedPage = computed(
  () => route.meta.studyRequired && route.name !== 'IchM11'
)
const canRender = computed(
  () =>
    !managedPage.value ||
    !route.params.study_id ||
    route.params.study_id === '*' ||
    route.params.study_id === studies.selectedStudy?.uid
)
const pageKey = computed(() =>
  managedPage.value
    ? JSON.stringify([studies.selectedStudy?.uid, studies.selectedStudyVersion])
    : undefined
)
</script>

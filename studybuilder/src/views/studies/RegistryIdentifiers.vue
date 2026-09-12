<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('StudyRegistryIdentifiersView.title') }} ({{ studyId }})
      <HelpButton :help-text="$t('_help.RegistryIdentifiersTable.general')" />
    </div>
    <RegistryIdentifiersSummary />
    <CommentThreadList
      :topic-path="'/studies/' + selectedStudy.uid + '/registry_identifiers'"
      :is-transparent="false"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import RegistryIdentifiersSummary from '@/components/studies/RegistryIdentifiersSummary.vue'
import HelpButton from '@/components/tools/HelpButton.vue'
import CommentThreadList from '@/components/tools/CommentThreadList.vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'

const studiesGeneralStore = useStudiesGeneralStore()

const selectedStudy = computed(() => studiesGeneralStore.selectedStudy)
const studyId = computed(() => studiesGeneralStore.studyId)
</script>

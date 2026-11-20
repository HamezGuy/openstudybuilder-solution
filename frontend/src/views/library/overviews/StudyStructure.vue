<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('StudyStructuresOverview.title') }}
      <HelpButton :help-text="$t('_help.StudyStructuresOverview.general')" />
    </div>
    <v-alert v-if="readError" type="warning" variant="tonal">
      {{ $t('_global.listing_read_failed') }}
      <v-btn variant="text" @click="table.filterTable()">{{
        $t('_global.retry')
      }}</v-btn>
    </v-alert>
    <NNTable
      ref="table"
      table-id="library-study-structures-overview-table"
      :headers="headers"
      :items="items"
      :items-length="total"
      column-data-resource="studies/structure-overview"
      hide-default-switches
      item-value="uid"
      :export-config="{
        objectLabel: 'StudyStructuresOverview',
        dataUrl: 'studies/structure-overview',
      }"
      @filter="fetchStructures"
    >
      <template #[`item.study_ids`]="{ item }">
        <div v-html="sanitizeHTML(item.study_ids)" />
      </template>
    </NNTable>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import NNTable from '@/components/tools/NNTable.vue'
import studyApi from '@/api/study'
import filteringParameters from '@/utils/filteringParameters'
import HelpButton from '@/components/tools/HelpButton.vue'
import { escapeHTML, sanitizeHTML } from '@/utils/sanitize'

const { t } = useI18n()

const table = ref()
const readError = ref(false)
const items = ref([])
const total = ref(0)

const headers = [
  { title: t('StudyStructuresOverview.arms'), key: 'arms' },
  {
    title: t('StudyStructuresOverview.pre_treatment_epochs'),
    key: 'pre_treatment_epochs',
  },
  {
    title: t('StudyStructuresOverview.treatment_epochs'),
    key: 'treatment_epochs',
  },
  {
    title: t('StudyStructuresOverview.post_treatment_epochs'),
    key: 'post_treatment_epochs',
  },
  {
    title: t('StudyStructuresOverview.no_treatment_epochs'),
    key: 'no_treatment_epochs',
  },
  {
    title: t('StudyStructuresOverview.no_treatment_elements'),
    key: 'no_treatment_elements',
  },
  {
    title: t('StudyStructuresOverview.treatment_elements'),
    key: 'treatment_elements',
  },
  { title: t('StudyStructuresOverview.cohorts'), key: 'cohorts_in_study' },
  {
    title: t('StudyStructuresOverview.study_ids'),
    key: 'study_ids',
    width: '600px',
  },
]

function fetchStructures(filters, options, filtersUpdated) {
  const params = filteringParameters.prepareParameters(
    options,
    filters,
    filtersUpdated
  )
  readError.value = false
  studyApi
    .getStructureOverview(params)
    .then((resp) => {
      items.value = resp.data.items.map((elm) => ({
        ...elm,
        study_ids: elm.study_ids
          .map(escapeHTML)
          .map((x) => `<span style="white-space: nowrap;">${x}</span>`)
          .join(', '),
      }))
      total.value = resp.data.total
    })
    .catch(() => {
      readError.value = true
      items.value = []
      total.value = 0
    })
}
</script>

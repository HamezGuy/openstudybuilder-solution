<template>
  <NNTable
    :headers="headers"
    :items="items"
    :items-length="total"
    :history-data-fetcher="fetchAuditTrail"
    :history-title="$t('_global.audit_trail')"
    history-change-field="change_description"
    item-value="uid"
    export-object-label="ActivityItemClasses"
    export-data-url="activity-item-classes"
    column-data-resource="activity-item-classes"
    @filter="fetchItems"
  >
    <template #[`item.name`]="{ item }">
      <router-link
        :to="{ name: 'ActivityItemClassOverview', params: { id: item.uid } }"
        class="text-decoration-none"
      >
        {{ item.name }}
      </router-link>
    </template>
    <template #[`item.nci_concept_id`]="{ item }">
      <NCIConceptLink :concept-id="item.nci_concept_id" />
    </template>
    <template #[`item.start_date`]="{ item }">
      <v-tooltip location="top">
        <template #activator="{ props }">
          <span v-bind="props">{{
            $filters.dateRelative(item.start_date)
          }}</span>
        </template>
        {{ $filters.date(item.start_date) }}
      </v-tooltip>
    </template>
    <template #[`item.status`]="{ item }">
      <StatusChip :status="item.status" />
    </template>
  </NNTable>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import NCIConceptLink from '@/components/tools/NCIConceptLink.vue'
import NNTable from '@/components/tools/NNTable.vue'
import StatusChip from '@/components/tools/StatusChip.vue'
import api from '@/api/activityItemClasses'
import filteringParameters from '@/utils/filteringParameters'

const { t } = useI18n()

const items = ref([])
const total = ref(0)

const headers = [
  { key: 'name', title: t('_global.name') },
  { key: 'definition', title: t('_global.definition') },
  { key: 'nci_concept_id', title: t('ActivityItemClassTable.nci_concept_id') },
  { key: 'start_date', title: t('_global.modified') },
  { key: 'author_username', title: t('_global.modified_by') },
  { key: 'version', title: t('_global.version') },
  { key: 'status', title: t('_global.status') },
]

const fetchItems = (filters, options, filtersUpdated) => {
  const params = filteringParameters.prepareParameters(
    options,
    filters,
    filtersUpdated
  )
  api.getAll(params).then((resp) => {
    items.value = resp.data.items
    total.value = resp.data.total
  })
}

const fetchAuditTrail = async (options) => {
  const data = {
    page_number: options.page,
    page_size: options.itemsPerPage,
    total_count: true,
  }
  const resp = await api.getVersions(data)
  return resp.data
}
</script>

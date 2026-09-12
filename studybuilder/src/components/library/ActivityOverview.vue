<template>
  <div class="activity-overview-container">
    <BaseActivityOverview
      ref="overview"
      :transform-func="transformItem"
      :navigate-to-version="changeVersion"
      :history-headers="historyHeaders"
      v-bind="$attrs"
      @refresh="refreshData"
    >
      <template #htmlContent="{ itemOverview, item }">
        <!-- Activity Summary -->
        <ActivitySummary
          v-if="itemOverview && itemOverview.activity"
          :activity="itemOverview.activity"
          :used-studies="item.used_by_studies"
          :all-versions="allVersions(itemOverview)"
          :show-author="true"
          class="activity-summary"
          @version-change="(value) => manualChangeVersion(value)"
        >
          <template #content>
            <ActivityGroupings
              :item-data="itemOverview"
              :activity-id="$route.params.id"
              :version="$route.params.version"
              @refresh="refreshData"
            />
          </template>
        </ActivitySummary>

        <v-row
          v-if="itemOverview && itemOverview.activity"
          class="activity-section"
        >
          <v-col cols="12" class="px-0">
            <div class="section-header mb-1">
              <h3 class="text-headline-small font-weight-bold text-primary">
                {{ $t('ActivityOverview.instances') }}
              </h3>
            </div>

            <ActivityInstancesTable
              :item-data="itemOverview"
              :activity-groupings="itemOverview.activity_groupings || []"
              :activity-id="$route.params.id"
              :version="$route.params.version"
            />
          </v-col>
        </v-row>
      </template>
      <template #itemForm="{ show, item, close }">
        <ActivitiesForm :open="show" :edited-activity="item" @close="close" />
      </template>
    </BaseActivityOverview>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'
import BaseActivityOverview from '@/components/library/BaseActivityOverview.vue'
import ActivitySummary from '@/components/library/ActivitySummary.vue'
import ActivityGroupings from '@/components/library/ActivityGroupings.vue'
import ActivityInstancesTable from '@/components/library/ActivityInstancesTable.vue'
import activities from '@/api/activities'

const { t } = useI18n()
const appStore = useAppStore()
const route = useRoute()
const router = useRouter()

const overview = ref(null)
const versionDetail = ref(null)
const paginationTotal = ref(0)
const tableOptions = ref({
  page: 1,
  itemsPerPage: 10,
  sortBy: [],
  sortDesc: [],
})

import { defineAsyncComponent } from 'vue'

const emit = defineEmits(['refresh'])
const ActivitiesForm = defineAsyncComponent(
  () => import('@/components/library/ActivitiesForm.vue')
)

function refreshData() {
  // Refresh the base activity overview
  if (overview.value) {
    overview.value.refreshData()
  }
}

const historyHeaders = [
  { title: t('_global.library'), key: 'library_name' },
  {
    title: t('ActivityTable.activity_group'),
    key: 'activity_group.name',
    externalFilterSource: 'concepts/activities/activity-groups$name',
    width: '15%',
    exludeFromHeader: ['is_data_collected'],
  },
  {
    title: t('ActivityTable.activity_subgroup'),
    key: 'activity_subgroup.name',
    externalFilterSource: 'concepts/activities/activity-sub-groups$name',
    width: '15%',
    exludeFromHeader: ['is_data_collected'],
  },
  {
    title: t('ActivityTable.activity_name'),
    key: 'name',
    externalFilterSource: 'concepts/activities/activities$name',
  },
  {
    title: t('ActivityTable.sentence_case_name'),
    key: 'name_sentence_case',
  },
  {
    title: t('ActivityTable.synonyms'),
    key: 'synonyms',
  },
  { title: t('ActivityTable.abbreviation'), key: 'abbreviation' },
  {
    title: t('ActivityTable.is_data_collected'),
    key: 'is_data_collected',
  },
  { title: t('_global.modified'), key: 'start_date' },
  { title: t('_global.status'), key: 'status' },
  { title: t('_global.version'), key: 'version' },
]

function allVersions(item) {
  if (!item || !item.all_versions) return []
  return [...item.all_versions]
}

async function fetchVersionDetail(activityUid, version, options = {}) {
  try {
    const params = {
      page_number: options.page || tableOptions.value.page,
      page_size: options.itemsPerPage || tableOptions.value.itemsPerPage,
      total_count: true,
    }

    // If explicitly called with a version, use getVersionDetail
    if (version) {
      const response = await activities.getVersionDetail(
        activityUid,
        version,
        params
      )
      versionDetail.value = response.data
      paginationTotal.value = response.data.total || 0
    }
    // If no version is provided but the overview has loaded
    else if (overview.value?.itemOverview?.activity?.version) {
      // Get the current version from overview
      const currentVersion = overview.value.itemOverview.activity.version

      // Use that version to fetch the details
      const response = await activities.getVersionDetail(
        activityUid,
        currentVersion,
        params
      )
      versionDetail.value = response.data
      paginationTotal.value = response.data.total || 0
    }
  } catch (error) {
    console.error('Error fetching version detail:', error)
    versionDetail.value = null
    paginationTotal.value = 0
  }
}

// Special function for the ActivitySummary component
function manualChangeVersion(version) {
  // Use route.params.id directly which contains the current activity's ID
  if (!route.params.id) {
    console.error('Cannot change version: route.params.id is missing')
    return
  }

  // Fetch version detail data
  fetchVersionDetail(route.params.id, version)

  // Clone the approach from the original but use route.params.id
  const activityWithId = {
    uid: route.params.id,
  }

  changeVersion(activityWithId, version)
}

// Original changeVersion function, unchanged from the original
async function changeVersion(activity, version) {
  if (!activity || !activity.uid) return

  try {
    await router.push({
      name: 'ActivityOverview',
      params: { id: activity.uid, version },
    })
    emit('refresh')
  } catch (error) {
    console.error('Error navigating to new version:', error)
  }
}

function transformItem(item) {
  if (!item) return

  if (item.activity_groupings && item.activity_groupings.length > 0) {
    const groups = []
    const subgroups = []
    for (const grouping of item.activity_groupings) {
      groups.push(grouping.activity_group_name)
      subgroups.push(grouping.activity_subgroup_name)
    }
    item.activity_group = { name: groups }
    item.activity_subgroup = { name: subgroups }
    item.item_key = item.uid
  } else {
    item.activity_group = { name: [] }
    item.activity_subgroup = { name: [] }
    item.item_key = item.uid
  }
}

onMounted(() => {
  appStore.addBreadcrumbsLevel(
    t('Sidebar.library.concepts'),
    { name: 'Activities' },
    1,
    false
  )

  appStore.addBreadcrumbsLevel(
    t('Sidebar.library.activities'),
    { name: 'Activities' },
    2,
    true
  )

  appStore.addBreadcrumbsLevel(
    t('Sidebar.library.activities'),
    { name: 'Activities', params: { tab: 'activities' } },
    3,
    true
  )

  const activityName = overview.value?.itemOverview?.activity?.name || ''

  appStore.addBreadcrumbsLevel(
    activityName || 'Activity',
    {
      name: 'ActivityOverview',
      params: { id: route.params.id },
    },
    4,
    true
  )
})
</script>

<style scoped>
.activity-overview-container {
  width: 100%;
  background-color: transparent;
}

/* Activity section styling */
.activity-section {
  margin: 0 !important;
  width: 100%;
}

/* Adjusts the spacing for section headers */
.section-header {
  margin-top: 16px;
  margin-bottom: 8px;
  padding-left: 0;
}

/* Ensures tables have a clean design */
:deep(.table-container) {
  width: 100%;
  margin-bottom: 24px;
  border-radius: 4px;
  background-color: transparent;
  box-shadow: none;
  overflow: hidden;
}

/* Remove card styling from tables */
:deep(.table-container .v-card) {
  width: 100% !important;
  margin: 0 !important;
  box-shadow: none !important;
  border-radius: 0 !important;
  background-color: transparent !important;
}

/* Consistent padding for card title */
:deep(.table-container .v-card__title) {
  padding-left: 16px;
  padding-right: 16px;
}

/* Ensure that tables take full width */
:deep(.activity-section .v-data-table),
:deep(.activity-section .v-table),
:deep(.activity-section table) {
  width: 100% !important;
  table-layout: fixed !important;
}

/* Set column widths based on header definitions */
:deep(.activity-section th),
:deep(.activity-section td) {
  width: var(--width, 33%) !important;
}

/* Cell padding consistency */
:deep(.table-container .v-data-table__td),
:deep(.table-container .v-data-table__th) {
  padding: 12px 16px !important;
}

/* Ensure card content takes full width */
:deep(.activity-section .v-card-text) {
  width: 100% !important;
  padding: 0 !important;
}

/* Fix table wrapper to auto-height */
:deep(.v-table__wrapper) {
  height: auto !important;
}

/* Round table corners */
.activity-overview-container :deep(.v-data-table) {
  border-radius: 8px !important;
  overflow: visible;
}

.activity-overview-container :deep(.v-table__wrapper) {
  border-radius: 8px !important;
  overflow-x: auto;
}

/* Table styling overrides that penetrate component boundaries */
.activity-overview-container :deep(.v-table),
.activities-row :deep(.v-table) {
  background: transparent !important;
}

.activity-overview-container :deep(.v-data-table__td),
.activities-row :deep(.v-data-table__td) {
  background-color: white !important;
}

.activity-overview-container :deep(.v-data-table__th),
.activities-row :deep(.v-data-table__th) {
  background-color: rgb(var(--v-theme-nnTrueBlue)) !important;
}

.activity-overview-container :deep(.v-data-table__tbody tr),
.activities-row :deep(.v-data-table__tbody tr) {
  background-color: white !important;
}

.activity-overview-container :deep(.v-card),
.activity-overview-container :deep(.v-sheet),
.activities-row :deep(.v-card),
.activities-row :deep(.v-sheet) {
  background-color: transparent !important;
  box-shadow: none !important;
}
</style>

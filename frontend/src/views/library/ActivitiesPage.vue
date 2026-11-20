<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('Sidebar.library.activities') }}
      <HelpButtonWithPanels
        :help-text="$t('_help.ActivitiesTable.general')"
        :items="helpItems"
      />
    </div>
    <NavigationTabs :tabs="tabs">
      <template #default="{ tabKeys, activePanel }">
        <v-window-item value="activities">
          <template v-if="activePanel === 'activities'">
            <ActivitiesTable
              :key="`activities-${tabKeys['activities']}`"
              source="activities"
            />
          </template>
        </v-window-item>
        <v-window-item value="activity-groups">
          <template v-if="activePanel === 'activity-groups'">
            <ActivitiesTable
              :key="`activities-${tabKeys['activity-groups']}`"
              source="activity-groups"
            />
          </template>
        </v-window-item>
        <v-window-item value="activity-subgroups">
          <template v-if="activePanel === 'activity-subgroups'">
            <ActivitiesTable
              :key="`activities-${tabKeys['activity-subgroups']}`"
              source="activity-sub-groups"
            />
          </template>
        </v-window-item>
        <v-window-item value="activities-by-grouping">
          <template v-if="activePanel === 'activities-by-grouping'">
            <ActivitiesTable
              :key="`activities-${tabKeys['activities-by-grouping']}`"
              source="activities-by-grouping"
            />
          </template>
        </v-window-item>
        <v-window-item value="activity-instances">
          <template v-if="activePanel === 'activity-instances'">
            <ActivitiesTable
              :key="`activities-${tabKeys['activity-instances']}`"
              source="activity-instances"
            />
          </template>
        </v-window-item>
        <v-window-item value="requested-activities">
          <template v-if="activePanel === 'requested-activities'">
            <ActivitiesTable
              :key="`activities-${tabKeys['requested-activities']}`"
              source="activities"
              requested
            />
          </template>
        </v-window-item>
        <v-window-item value="activity-instance-classes">
          <template v-if="activePanel === 'activity-instance-classes'">
            <Suspense>
              <ActivityInstanceClassTable />
            </Suspense>
          </template>
        </v-window-item>
        <v-window-item value="activity-item-classes">
          <template v-if="activePanel === 'activity-item-classes'">
            <Suspense>
              <ActivityItemClassTable />
            </Suspense>
          </template>
        </v-window-item>
        <v-window-item v-if="nsvsLibrary" value="non-standard-variables">
          <template v-if="activePanel === 'non-standard-variables'">
            <Suspense>
              <NonStandardVariablesTable />
            </Suspense>
          </template>
        </v-window-item>
      </template>
    </NavigationTabs>
  </div>
</template>

<script setup>
import ActivitiesTable from '@/components/library/ActivitiesTable.vue'
import ActivityInstanceClassTable from '@/components/library/ActivityInstanceClassTable.vue'
import ActivityItemClassTable from '@/components/library/ActivityItemClassTable.vue'
import NonStandardVariablesTable from '@/components/library/NonStandardVariablesTable.vue'
import HelpButtonWithPanels from '@/components/tools/HelpButtonWithPanels.vue'
import NavigationTabs from '@/components/tools/NavigationTabs.vue'
import { useI18n } from 'vue-i18n'
import { useFeatureFlagsStore } from '@/stores/feature-flags'
import { computed } from 'vue'

const { t } = useI18n()
const featureFlagsStore = useFeatureFlagsStore()

const streamlinePlaceholders = computed(() =>
  featureFlagsStore.getFeatureFlag('streamline_placeholder_activities')
)
const nsvsLibrary = computed(() =>
  featureFlagsStore.getFeatureFlag('nsvs_library')
)

const tabs = computed(() => {
  const allTabs = [
    { tab: 'activities', name: t('ActivityTable.activities') },
    {
      tab: 'activity-groups',
      name: t('ActivityTable.activity_groups'),
    },
    {
      tab: 'activity-subgroups',
      name: t('ActivityTable.activity_subgroups'),
    },
    {
      tab: 'activities-by-grouping',
      name: t('ActivityTable.activities_overview'),
    },
    { tab: 'activity-instances', name: t('ActivityTable.instances') },
    ...(!streamlinePlaceholders.value
      ? [
          {
            tab: 'requested-activities',
            name: t('ActivityTable.requested'),
          },
        ]
      : []),
    {
      tab: 'activity-instance-classes',
      name: t('ActivityTable.activity_instance_classes'),
    },
    {
      tab: 'activity-item-classes',
      name: t('ActivityTable.activity_item_classes'),
    },
    ...(nsvsLibrary.value
      ? [
          {
            tab: 'non-standard-variables',
            name: t('ActivityTable.non_standard_variables'),
          },
        ]
      : []),
  ]
  return allTabs
})
const helpItems = [
  'ActivityTable.activities',
  'ActivityTable.activity_groups',
  'ActivityTable.activity_subgroups',
  'ActivityTable.activities_overview',
  'ActivityTable.instances',
  'ActivityTable.requested',
  'ActivityTable.activity_name',
  'ActivityTable.sentence_case_name',
  'ActivityTable.abbreviation',
  'ActivityTable.definition',
  'ActivityTable.nci_concept_id',
  'ActivityTable.topic_code',
  'ActivityTable.adam_code',
  'ActivityTable.activity_group',
  'ActivityTable.activity_subgroup',
  'ActivityTable.is_data_collected',
  'ActivityTable.activity',
  'ActivityTable.instance',
  'ActivityTable.is_required_for_activity',
  'ActivityTable.is_default_selected_for_activity',
  'ActivityTable.is_data_sharing',
  'ActivityTable.is_legacy_usage',
  'ActivityTable.rationale_for_request',
]
</script>

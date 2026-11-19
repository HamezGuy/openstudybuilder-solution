<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('StudyDataStandardVersionsView.title') }}
    </div>
    <NavigationTabs :tabs="tabs">
      <template #default="{ tabKeys }">
        <v-window-item value="controlled_terminology">
          <CTStandardVersionsTable
            :key="`controlled_terminology-${tabKeys.controlled_terminology}`"
          />
        </v-window-item>
        <v-window-item value="dictionaries">
          <StudyStandardEvidence kind="dictionaries" />
        </v-window-item>
        <v-window-item value="data_exchange">
          <StudyStandardEvidence kind="data_exchange" />
        </v-window-item>
      </template>
    </NavigationTabs>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import CTStandardVersionsTable from '@/components/studies/dataStandardVersions/CTStandardVersionsTable.vue'
import NavigationTabs from '@/components/tools/NavigationTabs.vue'
import StudyStandardEvidence from '@/components/studies/dataStandardVersions/StudyStandardEvidence.vue'

const { t } = useI18n()

const tabs = [
  {
    tab: 'controlled_terminology',
    name: t('StudyDataStandardVersionsView.tab1_title'),
  },
  { tab: 'dictionaries', name: t('StudyDataStandardVersionsView.tab2_title') },
  { tab: 'data_exchange', name: t('StudyDataStandardVersionsView.tab3_title') },
]
</script>

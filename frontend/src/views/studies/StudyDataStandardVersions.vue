<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('StudyDataStandardVersionsView.title') }}
    </div>
    <NavigationTabs :tabs="tabs">
      <template #default="{ tabKeys, activePanel }">
        <v-window-item value="controlled_terminology">
          <template v-if="activePanel === 'controlled_terminology'">
            <CTStandardVersionsTable
              :key="`controlled_terminology-${tabKeys.controlled_terminology}`"
            />
          </template>
        </v-window-item>
        <v-window-item value="dictionaries">
          <template v-if="activePanel === 'dictionaries'">
            <StudyStandardEvidence kind="dictionaries" />
          </template>
        </v-window-item>
        <v-window-item value="data_exchange">
          <template v-if="activePanel === 'data_exchange'">
            <StudyStandardEvidence kind="data_exchange" />
          </template>
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

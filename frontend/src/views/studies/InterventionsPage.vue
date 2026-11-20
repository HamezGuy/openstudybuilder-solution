<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('Sidebar.study.study_interventions') }} ({{
        studiesGeneralStore.studyId
      }})
      <HelpButton :help-text="$t('_help.StudyInterventionsTable.general')" />
    </div>
    <NavigationTabs :tabs="tabs">
      <template #default="{ activePanel }">
        <v-window-item value="overview">
          <template v-if="activePanel === 'overview'">
            <InterventionOverview />
          </template>
        </v-window-item>
        <v-window-item value="study_compounds">
          <template v-if="activePanel === 'study_compounds'">
            <CompoundTable />
          </template>
        </v-window-item>
        <v-window-item value="study_compound_dosings">
          <template v-if="activePanel === 'study_compound_dosings'">
            <CompoundDosingTable />
          </template>
        </v-window-item>
        <v-window-item value="other_interventions">
          <template v-if="activePanel === 'other_interventions'">
            <h2 class="text-title-large ma-4">
              {{ $t('InterventionOverview.design_by_epoch') }}
            </h2>
            <StudyStructureOverview />
          </template>
        </v-window-item>
      </template>
    </NavigationTabs>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import CompoundDosingTable from '@/components/studies/CompoundDosingTable.vue'
import CompoundTable from '@/components/studies/CompoundTable.vue'
import InterventionOverview from '@/components/studies/InterventionOverview.vue'
import StudyStructureOverview from '@/components/studies/StudyStructureOverview.vue'
import HelpButton from '@/components/tools/HelpButton.vue'
import NavigationTabs from '@/components/tools/NavigationTabs.vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'

const { t } = useI18n()
const studiesGeneralStore = useStudiesGeneralStore()

const tabs = [
  {
    tab: 'overview',
    name: t('Sidebar.study.study_intervention_overview'),
  },
  { tab: 'study_compounds', name: t('Sidebar.study.compounds') },
  {
    tab: 'study_compound_dosings',
    name: t('Sidebar.study.compound_dosings'),
  },
  {
    tab: 'other_interventions',
    name: t('Sidebar.study.other_interventions'),
  },
]
</script>

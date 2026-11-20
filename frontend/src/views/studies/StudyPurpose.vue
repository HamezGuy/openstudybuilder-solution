<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('Sidebar.study.purpose') }} ({{ studyId }})
      <HelpButtonWithPanels
        :help-text="$t('_help.StudyPurposeView.general')"
        :items="helpItems"
      />
    </div>
    <NavigationTabs :tabs="tabs" @tab-changed="tab = $event">
      <template #default="{ tabKeys, activePanel }">
        <v-window-item value="objectives">
          <template v-if="activePanel === 'objectives'">
            <ObjectiveTable :key="`objectives-${tabKeys.objectives}`" />
          </template>
        </v-window-item>
        <v-window-item value="endpoints">
          <template v-if="activePanel === 'endpoints'">
            <EndpointTable :key="`endpoints-${tabKeys.endpoints}`" />
          </template>
        </v-window-item>
        <v-window-item value="estimands">
          <template v-if="activePanel === 'estimands'">
            <UnderConstruction />
          </template>
        </v-window-item>
      </template>
    </NavigationTabs>
  </div>
</template>

<script>
import EndpointTable from '@/components/studies/EndpointTable.vue'
import ObjectiveTable from '@/components/studies/ObjectiveTable.vue'
import UnderConstruction from '@/components/layout/UnderConstruction.vue'
import HelpButtonWithPanels from '@/components/tools/HelpButtonWithPanels.vue'
import NavigationTabs from '@/components/tools/NavigationTabs.vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import { computed } from 'vue'

export default {
  components: {
    NavigationTabs,
    EndpointTable,
    HelpButtonWithPanels,
    ObjectiveTable,
    UnderConstruction,
  },
  setup() {
    const studiesGeneralStore = useStudiesGeneralStore()
    return {
      studyId: computed(() => studiesGeneralStore.studyId),
    }
  },
  data() {
    return {
      tab: null,
      helpItems: [
        'StudyPurposeView.study_objectives',
        'StudyPurposeView.study_endpoints',
        'StudyPurposeView.study_estimands',
      ],
      tabs: [
        {
          tab: 'objectives',
          name: this.$t('StudyPurposeView.study_objectives'),
        },
        { tab: 'endpoints', name: this.$t('StudyPurposeView.study_endpoints') },
        // { tab: 'estimands', name: this.$t('StudyPurposeView.study_estimands') },
      ],
    }
  },
  watch: {
    tab(newValue) {
      this.helpItems.splice(3, 4)
      if (newValue === 'objectives') {
        this.helpItems.push(
          'StudyObjectivesTable.objective_level',
          'StudyObjectivesTable.endpoint_count',
          'StudyObjectivesTable.objective'
        )
      } else if (newValue === 'endpoints') {
        this.helpItems.push(
          'StudyEndpointsTable.endpoint_title',
          'StudyEndpointsTable.objective',
          'StudyEndpointsTable.time_frame',
          'StudyEndpointsTable.units'
        )
      }
    },
  },
}
</script>

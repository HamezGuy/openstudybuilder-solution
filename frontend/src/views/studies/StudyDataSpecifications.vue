<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('StudyDataSpecifications.title') }} ({{
        studiesGeneralStore.studyId
      }})
      <HelpButtonWithPanels :items="helpItems" />
      <v-spacer />
      <v-btn
        variant="outlined"
        icon
        size="small"
        color="nnBaseBlue"
        :disabled="lockSettings"
        :loading="soaContentLoadingStore.loading"
        @click="openSoaSettings"
      >
        <v-icon left>mdi-cog-outline</v-icon>
        <v-tooltip activator="parent" location="top">
          {{ $t('ProtocolFlowchart.soa_settings') }}
        </v-tooltip>
      </v-btn>
    </div>
    <NavigationTabs :tabs="tabs">
      <template #default="{ tabKeys }">
        <v-window-item value="instances">
          <StudyActivityInstancesTable
            :key="`instances-${tabKeys.instances}`"
          />
        </v-window-item>
        <v-window-item value="operational">
          <OperationalSoa
            :key="`operational-${tabKeys.operational}`"
            :update="updateSoa"
          />
        </v-window-item>
      </template>
    </NavigationTabs>
    <v-dialog v-model="showSoaSettings" max-width="800px">
      <SoaSettingsForm @close="closeSoaSettings" />
    </v-dialog>
  </div>
</template>

<script setup>
import StudyActivityInstancesTable from '@/components/studies/StudyActivityInstancesTable.vue'
import OperationalSoa from '@/components/studies/OperationalSoa.vue'
import SoaSettingsForm from '@/components/studies/SoaSettingsForm.vue'
import HelpButtonWithPanels from '@/components/tools/HelpButtonWithPanels.vue'
import NavigationTabs from '@/components/tools/NavigationTabs.vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import { useI18n } from 'vue-i18n'
import { inject, computed, ref } from 'vue'
import { useAccessGuard } from '@/composables/accessGuard'
import { useSoaContentLoadingStore } from '@/stores/soa-content-loading'

const studiesGeneralStore = useStudiesGeneralStore()
const soaContentLoadingStore = useSoaContentLoadingStore()
const accessGuard = useAccessGuard()
const { t } = useI18n()
const roles = inject('roles')

const tabs = [
  { tab: 'instances', name: t('StudyDataSpecifications.tab1_title') },
  {
    tab: 'operational',
    name: t('StudyDataSpecifications.tab2_title'),
    loading: () => (soaContentLoadingStore.loading ? 'warning' : false),
  },
]
const showSoaSettings = ref(false)
const updateSoa = ref(0)
const helpItems = [
  'StudyDataSpecifications.general',
  'StudyDataSpecifications.instances_actions',
  'StudyDataSpecifications.instances_ellipsis',
  'StudyDataSpecifications.instances_exclamation',
  'StudyDataSpecifications.instances_updates',
]

const lockSettings = computed(() => {
  if (
    !accessGuard.checkPermission(roles.STUDY_WRITE) ||
    studiesGeneralStore.selectedStudyVersion !== null
  ) {
    return true
  }
  return false
})

function openSoaSettings() {
  showSoaSettings.value = true
}

function closeSoaSettings() {
  updateSoa.value++
  showSoaSettings.value = false
}
</script>

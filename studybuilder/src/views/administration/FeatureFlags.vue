<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('FeatureFlagsView.title') }}
    </div>
    <v-card>
      <v-card-text>
        <v-alert
          color="nnLightBlue200"
          icon="mdi-information-outline"
          class="text-nnTrueBlue mx-4 my-2"
        >
          {{ $t('FeatureFlagsView.help') }}
        </v-alert>

        <div class="d-flex mx-4 mt-4">
          <v-text-field
            v-model="search"
            v-text-field
            clearable
            clear-icon="mdi-close"
            prepend-inner-icon="mdi-magnify"
            :label="$t('_global.search')"
            single-line
            color="nnBaseBlue"
            hide-details
            style="min-width: 240px; max-width: 300px"
            class="searchFieldLabel ml-0"
            data-cy="search-field"
          />
          <v-spacer />
          <v-btn-toggle
            v-model="selectedSection"
            mandatory
            density="compact"
            color="nnBaseBlue"
            divided
            variant="outlined"
            class="layoutSelector"
            @update:model-value="onSectionChange"
          >
            <v-btn
              v-for="section in sections"
              :key="section.value"
              :value="section.value"
            >
              {{ section.label }}
            </v-btn>
          </v-btn-toggle>
          <v-spacer />
        </div>

        <v-data-table
          v-model:search="search"
          :headers="headers"
          :items="displayedFlags"
          class="mx-4 my-6"
          items-per-page="-1"
        >
          <template #[`item.actions`]="{ item }">
            <v-switch
              v-model="item.enabled"
              hide-details
              @update:model-value="(value) => toggleFlagState(item, value)"
            />
          </template>
          <template #[`item.name`]="{ item }">
            <div class="d-flex align-center justify-space-between">
              <span>{{ item.name }}</span>
              <v-menu
                open-on-hover
                :close-on-content-click="false"
                location="top"
              >
                <template #activator="{ props }">
                  <v-icon
                    v-bind="props"
                    size="small"
                    color="nnBaseBlue"
                    class="ml-2"
                  >
                    mdi-information-outline
                  </v-icon>
                </template>
                <v-card max-width="400">
                  <v-card-text>{{ item.description }}</v-card-text>
                </v-card>
              </v-menu>
            </div>
          </template>
          <template #bottom></template>
        </v-data-table>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import featureFlagsApi from '@/api/featureFlags'

const { t } = useI18n()

const flags = ref([])
const search = ref('')
const selectedSection = ref('studies')

const displayedFlags = computed(() => {
  return flags.value.filter((flag) => flag.section === selectedSection.value)
})

const headers = [
  { key: 'actions', title: '', width: '6%' },
  { key: 'feature', title: t('_global.feature'), width: '45%' },
  { key: 'name', title: t('_global.name'), width: '45%' },
]
const sections = [
  { value: 'studies', label: t('_global.studies') },
  { value: 'library', label: t('_global.library') },
  { value: 'admin', label: t('Topbar.admin') },
]

async function toggleFlagState(flag, value) {
  await featureFlagsApi.update(flag.sn, { enabled: value })
}

featureFlagsApi.get().then((resp) => {
  flags.value = resp.data
})
</script>

<style scoped>
.v-data-table {
  width: auto !important;
}
</style>

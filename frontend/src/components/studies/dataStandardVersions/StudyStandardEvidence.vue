<template>
  <section class="pa-4">
    <h2 class="text-h6">
      {{ label(isDictionary ? 'dictionary_selections' : 'exchange_outputs') }}
    </h2>
    <p class="mb-3">
      {{ label(isDictionary ? 'dictionary_note' : 'exchange_note') }}
    </p>
    <v-progress-linear v-if="loading" indeterminate />
    <v-alert v-else-if="loadError" type="error" role="alert">{{
      loadError
    }}</v-alert>
    <v-table v-else>
      <thead>
        <tr>
          <th scope="col">
            {{ label(isDictionary ? 'study_field' : 'format') }}
          </th>
          <th scope="col">
            {{ label(isDictionary ? 'selected_term' : 'source_records') }}
          </th>
          <th v-if="isDictionary" scope="col">{{ t('_global.uid') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key">
          <td>
            <router-link
              v-if="row.route"
              :to="{
                name: row.route,
                params: { study_id: store.selectedStudy.uid },
              }"
            >
              {{ row.label }}
            </router-link>
            <template v-else>{{ row.label }}</template>
          </td>
          <td>{{ row.value }}</td>
          <td v-if="isDictionary">{{ row.uid }}</td>
        </tr>
        <tr v-if="isDictionary && !rows.length">
          <td colspan="3">{{ label('dictionary_empty') }}</td>
        </tr>
      </tbody>
    </v-table>
    <v-btn
      v-if="loadError || rows.some((row) => row.failed)"
      class="mt-3"
      @click="retry++"
      >{{ t('_global.retry') }}</v-btn
    >
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import listings from '@/api/listings'
import study from '@/api/study'
import { useStudiesGeneralStore } from '@/stores/studies-general'

const props = defineProps({ kind: { type: String, required: true } })
const store = useStudiesGeneralStore()
const { t } = useI18n()
const label = (key, params) => t('StudyDataStandardVersionsView.' + key, params)
const isDictionary = computed(() => props.kind === 'dictionaries')
const rows = ref([])
const loading = ref(false)
const loadError = ref('')
const retry = ref(0)
const dictionaryFields = [
  ['therapeutic_area_codes', 'StudyPopulationForm.therapeuticarea'],
  [
    'disease_condition_or_indication_codes',
    'StudyPopulationForm.disease_condition',
  ],
  ['diagnosis_group_codes', 'StudyPopulationForm.diagnosis_group'],
]
const failure = (error) => error.response?.data?.message || label('load_error')

watch(
  [
    () => store.selectedStudy?.uid,
    () => store.selectedStudyVersion,
    isDictionary,
    retry,
  ],
  async ([uid, version, dictionaries], _previous, onCleanup) => {
    let current = true
    onCleanup(() => {
      current = false
    })
    rows.value = []
    loadError.value = ''
    loading.value = Boolean(uid)
    if (!uid) return
    try {
      if (dictionaries) {
        const { data } = await study.getStudyPopulationMetadata(uid)
        const population = data.current_metadata.study_population
        if (current)
          rows.value = dictionaryFields.flatMap(([field, title]) =>
            (population?.[field] || []).map((term) => ({
              key: field + ':' + term.term_uid,
              label: t(title),
              value: term.name,
              uid: term.term_uid,
            }))
          )
      } else {
        const params = {
          page_number: 1,
          page_size: 1,
          total_count: true,
          study_value_version: version,
        }
        const outputs = [
          [
            'trial_arm_rows',
            'SdtmStudyDesignDatasets',
            () => listings.getAllSdtm(uid, params, 'TA'),
          ],
          [
            'analysis_visit_rows',
            'AnalysisStudyMetadataNew',
            () => listings.getAllAdam(uid, 'mdvisit', params),
          ],
          ['usdm_export', 'Usdm', () => study.getDdfUsdmJson(uid, version)],
        ]
        const results = await Promise.all(
          outputs.map(async ([key, route, read]) => {
            const row = { key, label: label(key), route }
            try {
              const { data } = await read()
              return {
                ...row,
                value:
                  key === 'usdm_export'
                    ? label('document_available', { version: data.usdmVersion })
                    : data.total,
              }
            } catch (error) {
              return { ...row, failed: true, value: failure(error) }
            }
          })
        )
        if (current) rows.value = results
      }
    } catch (error) {
      if (current) loadError.value = failure(error)
    } finally {
      if (current) loading.value = false
    }
  },
  { immediate: true }
)
</script>

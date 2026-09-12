<template>
  <div>
    <v-row>
      <v-col cols="3">
        <v-card rounded="lg" variant="flat" class="timeline-height">
          <v-card-text>
            <v-timeline density="compact" class="">
              <v-timeline-item
                v-for="model of models"
                :key="model.name"
                :dot-color="
                  activeModel?.name === model.name ? 'primary' : 'grey'
                "
                size="small"
                right
              >
                <v-btn
                  variant="text"
                  :color="
                    activeModel?.name === model.name ? 'primary' : 'default'
                  "
                  @click="activeModel = model"
                >
                  {{ model.extended_implementation_guide }} -
                  {{ model.version }}
                </v-btn>
              </v-timeline-item>
            </v-timeline>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="9">
        <v-card v-if="activeModel" rounded="lg" variant="flat">
          <v-card-title>
            {{ $t('SponsorDataModels.datasets') }} - {{ activeModel.name }}
          </v-card-title>
          <v-card-text>
            <NNTable
              ref="datasetTable"
              key="datasetTable"
              :headers="datasetHeaders"
              :items="datasets"
              :items-length="totalDatasets"
              :items-per-page="itemsPerPage"
              :page="currentDatasetPage"
              :loading="loadingDatasets"
              :row-props="getRowProps"
              density="compact"
              no-padding
              no-title
              hide-search-field
              hide-actions-menu
              hide-default-switches
              @filter="fetchDatasets"
            >
              <template #[`item.select`]="{ item }">
                <v-radio-group v-model="selectedDataset" hide-details>
                  <v-radio :value="item.uid" />
                </v-radio-group>
              </template>
            </NNTable>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
    <v-row>
      <v-col>
        <v-card v-if="selectedDataset" rounded="lg" variant="flat">
          <v-card-text>
            <NNTable
              key="variableTable"
              :headers="variableHeaders"
              :default-headers="variableDefaultHeaders"
              :items="variables"
              :items-length="totalVariables"
              :page="currentVariablePage"
              :loading="loadingVariables"
              :row-props="getRowProps"
              export-object-label="DatasetVariables"
              density="compact"
              no-padding
              disable-filtering
              hide-search-field
              hide-default-switches
              @filter="fetchVariables"
            >
              <template #[`item.label`]="{ item }">
                {{ item.label }}
                <v-icon
                  v-if="item.dataset.key_order"
                  icon="mdi-key-outline"
                  :title="item.dataset.key_order"
                />
              </template>
              <template #[`item.is_cdisc_std`]="{ item }">
                {{ $filters.yesno(item.is_cdisc_std) }}
              </template>
              <template #[`item.include_in_raw`]="{ item }">
                {{ $filters.yesno(item.include_in_raw) }}
              </template>
              <template #[`item.nn_internal`]="{ item }">
                {{ $filters.yesno(item.nn_internal) }}
              </template>
              <template #[`item.qualifiers`]="{ item }">
                <template v-if="item.qualifiers">
                  {{ $filters.itemList(item.qualifiers) }}
                </template>
              </template>
              <template #[`item.referenced_codelists`]="{ item }">
                <template v-if="item.referenced_codelists">
                  <v-btn
                    v-for="codelist in item.referenced_codelists"
                    :key="codelist.uid"
                    variant="text"
                    @click="openCodelistDialog(codelist.uid)"
                  >
                    {{ codelist.submission_value }}
                  </v-btn>
                </template>
              </template>
            </NNTable>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
    <v-dialog v-model="showCodelistDialog" persistent>
      <StandardsCodelistTermsDialog
        :codelist-uid="selectedCodelistUid"
        @close="closeCodelistDialog"
      />
    </v-dialog>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import standardsApi from '@/api/standards'
import NNTable from '@/components/tools/NNTable.vue'
import filteringParameters from '@/utils/filteringParameters'
import StandardsCodelistTermsDialog from '@/components/library/StandardsCodelistTermsDialog.vue'

const props = defineProps({
  uid: {
    type: String,
    default: null,
  },
})

const { t } = useI18n()

const activeModel = ref(null)
const currentDatasetPage = ref(1)
const currentVariablePage = ref(1)
const datasets = ref([])
const datasetTable = ref()
const itemsPerPage = ref(5)
const loadingDatasets = ref(false)
const loadingVariables = ref(false)
const models = ref([])
const selectedCodelistUid = ref(null)
const selectedDataset = ref(null)
const showCodelistDialog = ref(false)
const totalDatasets = ref(0)
const totalVariables = ref(0)
const variables = ref([])

const datasetHeaders = [
  { key: 'select', title: '', width: '5%' },
  { key: 'uid', title: 'UID' },
  { key: 'label', title: t('_global.label') },
]

const variableHeaders = [
  { key: 'order', title: t('_global.order') },
  { key: 'uid', title: 'UID' },
  { key: 'label', title: t('_global.label') },
  { key: 'variable_type', title: t('SponsorDataModels.variable_type') },
  { key: 'length', title: t('SponsorDataModels.length') },
  { key: 'xml_datatype', title: t('SponsorDataModels.xml_datatype') },
  { key: 'role', title: t('SponsorDataModels.role') },
  { key: 'ig_comment', title: t('SponsorDataModels.ig_comment') },
  { key: 'is_basic_std', title: 'is_basic_std' },
  { key: 'display_format', title: 'display_format' },
  { key: 'core', title: 'core' },
  { key: 'origin', title: 'origin' },
  { key: 'origin_type', title: 'origin_type' },
  { key: 'origin_source', title: 'origin_source' },
  { key: 'term', title: 'term' },
  { key: 'algorithm', title: 'algorithm' },
  { key: 'is_cdisc_std', title: 'is_cdisc_std' },
  { key: 'comment', title: 'comment' },
  { key: 'class_table', title: 'class_table' },
  { key: 'class_column', title: 'class_column' },
  { key: 'map_var_flag', title: 'map_var_flag' },
  { key: 'fixed_mapping', title: 'fixed_mapping' },
  { key: 'include_in_raw', title: 'include_in_raw' },
  { key: 'nn_internal', title: 'nn_internal' },
  { key: 'value_lvl_where_cols', title: 'value_lvl_where_cols' },
  { key: 'value_lvl_label_col', title: 'value_lvl_label_col' },
  { key: 'value_lvl_collect_ct_val', title: 'value_lvl_collect_ct_val' },
  {
    key: 'value_lvl_ct_codelist_id_col',
    title: 'value_lvl_ct_codelist_id_col',
  },
  { key: 'enrich_build_order', title: 'enrich_build_order' },
  { key: 'enrich_rule', title: 'enrich_rule' },
  { key: 'qualifiers', title: 'qualifiers' },
  { key: 'referenced_codelists', title: 'referenced_codelists' },
]

const variableDefaultHeaders = [
  { key: 'uid', title: 'UID' },
  { key: 'label', title: t('_global.label') },
  { key: 'variable_type', title: t('SponsorDataModels.variable_type') },
  { key: 'length', title: t('SponsorDataModels.length') },
  { key: 'xml_datatype', title: t('SponsorDataModels.xml_datatype') },
  { key: 'role', title: t('SponsorDataModels.role') },
  { key: 'ig_comment', title: t('SponsorDataModels.ig_comment') },
]

async function fetchDatasets(filters, options, filtersUpdated) {
  const params = filteringParameters.prepareParameters(
    options,
    filters,
    filtersUpdated
  )
  if (!options) {
    params.page_size = itemsPerPage.value
    params.page_number = currentDatasetPage.value
  }
  if (!params.filters) {
    params.filters = {}
  }
  params.sponsor_model_name = activeModel.value.name
  params.sponsor_model_version = activeModel.value.version
  loadingDatasets.value = true
  try {
    const resp = await standardsApi.getSponsorModelDatasets(params)
    datasets.value = resp.data.items
    totalDatasets.value = resp.data.total
  } finally {
    loadingDatasets.value = false
  }
}

async function fetchVariables(filters, options, filtersUpdated) {
  const params = filteringParameters.prepareParameters(
    options,
    filters,
    filtersUpdated
  )
  if (!options) {
    variables.value = []
    currentVariablePage.value = 1
    params.page_number = currentVariablePage.value
  }
  if (!params.filters) {
    params.filters = {}
  }
  params.sponsor_model_name = activeModel.value.name
  params.sponsor_model_version = activeModel.value.version
  params.filters['dataset.uid'] = { v: [selectedDataset.value] }
  loadingVariables.value = true
  try {
    const resp = await standardsApi.getSponsorModelDatasetVariables(params)
    variables.value = resp.data.items
    totalVariables.value = resp.data.total
  } finally {
    loadingVariables.value = false
  }
}

function getRowProps(data) {
  const result = {}
  if (!data.item.is_basic_std) {
    result.class = 'bg-nnLightBlue200'
  }
  return result
}

function openCodelistDialog(codelistUid) {
  selectedCodelistUid.value = codelistUid
  showCodelistDialog.value = true
}

function closeCodelistDialog() {
  showCodelistDialog.value = false
}

watch(activeModel, (value, previousValue) => {
  currentDatasetPage.value = 1
  currentVariablePage.value = 1
  datasets.value = []
  variables.value = []
  selectedDataset.value = null
  datasetTable.value.filterTable
  if (previousValue) {
    fetchDatasets()
  }
})

watch(selectedDataset, (value, previousValue) => {
  if (previousValue) {
    fetchVariables()
  }
})

const resp = await standardsApi.getSponsorModels({
  filters: { uid: { v: [props.uid] } },
  page_size: 0,
})
models.value = resp.data.items
if (models.value.length) {
  activeModel.value = models.value[0]
}
</script>

<style>
.timeline-height {
  height: auto !important;
}
</style>

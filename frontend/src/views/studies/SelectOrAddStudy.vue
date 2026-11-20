<template>
  <div class="px-4">
    <div class="page-title d-flex align-center">
      {{ $t('StudyManageView.title') }}
      <HelpButton :help-text="$t('_help.SelectOrAddStudyTable.general')" />
    </div>
    <v-alert v-if="loadError" type="error" class="mb-4" role="alert">
      {{ loadError }}
      <v-btn variant="text" :disabled="studiesLoading" @click="reloadStudies">
        Retry
      </v-btn>
    </v-alert>
    <NavigationTabs
      ref="navigationTabs"
      :tabs="tabs"
      :breadcrumbs-level="2"
      @tab-changed="changePanel"
    >
      <template #default="{ tabKeys, activePanel }">
        <v-window-item :key="`active-${tabKeys.active}`" value="active">
          <template v-if="activePanel === 'active'">
            <StudyTable
              ref="activeStudiesTable"
              v-bind="$attrs"
              :items="paginatedStudies"
              :items-length="totalActiveStudies"
              :loading-watcher="studiesLoading"
              @filter="fetchActiveStudies"
              @refresh-studies="reloadStudies"
              @enable-filtering="openFiltering = !openFiltering"
              @sort="sort"
            >
              <template #customSearch>
                <v-text-field
                  v-model="searchString"
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
              </template>
              <template #customFiltering>
                <v-toolbar
                  v-if="openFiltering"
                  flat
                  class="filteringBar pt-1"
                  color="nnGray200"
                >
                  <v-slide-group show-arrows class="mb-5">
                    <v-autocomplete
                      v-for="header in headers"
                      :key="header.key"
                      ref="select"
                      v-model="columnFilters[header.key]"
                      clearable
                      multiple
                      width="240px"
                      :label="header.title"
                      color="nnBaseBlue"
                      bg-color="nnWhite"
                      class="filterAutocompleteLabel ml-1"
                      :items="getHeaderFilterData(header.key)"
                      clear-on-select
                      hide-details
                      single-line
                      @update:model-value="activeStudiesTable.filter()"
                    >
                      <template #selection="{ index }">
                        <div v-if="index === 0">
                          <span class="items-font-size">{{
                            typeof columnFilters[header.key][0] !== 'boolean' &&
                            typeof columnFilters[header.key][0] !== 'number' &&
                            columnFilters[header.key][0].length > 12
                              ? columnFilters[header.key][0].substring(0, 12) +
                                '...'
                              : columnFilters[header.key][0]
                          }}</span>
                        </div>
                        <span
                          v-if="index === 1"
                          class="text-grey text-body-small mr-1"
                        >
                          (+{{ columnFilters[header.key].length - 1 }})
                        </span>
                      </template>
                    </v-autocomplete>
                  </v-slide-group>
                  <v-spacer />
                  <v-btn
                    prepend-icon="mdi-close"
                    color="nnWhite"
                    variant="flat"
                    class="mr-3 mb-5 clearAllBtn"
                    rounded
                    :text="$t('NNTableTooltips.clear_filters_content')"
                    @click="clearFilters()"
                  />
                </v-toolbar>
              </template>
            </StudyTable>
          </template>
        </v-window-item>
        <v-window-item :key="`deleted-${tabKeys.deleted}`" value="deleted">
          <template v-if="activePanel === 'deleted'">
            <StudyTable
              ref="deletedStudiesTable"
              :items="paginatedStudies"
              :items-length="totalDeletedStudies"
              :loading-watcher="studiesLoading"
              read-only
              @filter="fetchDeletedStudies"
              @refresh-studies="reloadStudies"
              @enable-filtering="openFiltering = !openFiltering"
              @sort="sort"
            >
              <template #customSearch>
                <v-text-field
                  v-model="searchString"
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
              </template>
              <template #customFiltering>
                <v-toolbar
                  v-if="openFiltering"
                  flat
                  class="filteringBar pt-1"
                  color="nnGray200"
                >
                  <v-slide-group show-arrows class="mb-5">
                    <v-autocomplete
                      v-for="header in headers"
                      :key="header.key"
                      ref="select"
                      v-model="columnFilters[header.key]"
                      clearable
                      multiple
                      width="240px"
                      :label="header.title"
                      color="nnBaseBlue"
                      bg-color="nnWhite"
                      class="filterAutocompleteLabel ml-1"
                      :items="getHeaderFilterData(header.key)"
                      hide-details
                      single-line
                      @update:model-value="deletedStudiesTable.filter()"
                    >
                      <template #selection="{ index }">
                        <div v-if="index === 0">
                          <span class="items-font-size">{{
                            typeof columnFilters[header.key][0] !== 'boolean' &&
                            typeof columnFilters[header.key][0] !== 'number' &&
                            columnFilters[header.key][0].length > 12
                              ? columnFilters[header.key][0].substring(0, 12) +
                                '...'
                              : columnFilters[header.key][0]
                          }}</span>
                        </div>
                        <span
                          v-if="index === 1"
                          class="text-grey text-body-small mr-1"
                        >
                          (+{{ columnFilters[header.key].length - 1 }})
                        </span>
                      </template>
                    </v-autocomplete>
                  </v-slide-group>
                  <v-spacer />
                  <v-btn
                    prepend-icon="mdi-close"
                    color="nnWhite"
                    variant="flat"
                    class="mr-3 mb-5 clearAllBtn"
                    rounded
                    :text="$t('NNTableTooltips.clear_filters_content')"
                    @click="clearFilters()"
                  />
                </v-toolbar>
              </template>
            </StudyTable>
          </template>
        </v-window-item>
      </template>
    </NavigationTabs>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import api from '@/api/study'
import filteringParameters from '@/utils/filteringParameters'
import StudyTable from '@/components/studies/StudyTable.vue'
import HelpButton from '@/components/tools/HelpButton.vue'
import NavigationTabs from '@/components/tools/NavigationTabs.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const rows = ref(null)
let rowsFor = null
let pending = null
let readVersion = 0
const totalActiveStudies = ref(0)
const totalDeletedStudies = ref(0)
const savedFilters = ref('')
const searchString = ref('')
const filteredStudies = ref([])
const paginatedStudies = ref([])
const columnFilters = ref({})
const openFiltering = ref(false)
const fullRefresh = ref(false)
const studiesLoading = ref(true)
const loadError = ref('')

const headers = [
  {
    title: t('StudyTable.clinical_programme'),
    key: 'clinical_programme_name',
  },
  {
    title: t('StudyTable.project_id'),
    key: 'project_number',
  },
  {
    title: t('StudyTable.project_name'),
    key: 'project_name',
  },
  {
    title: t('StudyTable.number'),
    key: 'number',
  },
  {
    title: t('StudyTable.id'),
    key: 'id',
  },
  {
    title: t('StudyTable.subpart_id'),
    key: 'subpart_id',
  },
  {
    title: t('StudyTable.acronym'),
    key: 'acronym',
  },
  {
    title: t('StudyTable.subpart_acronym'),
    key: 'subpart_acronym',
  },
  {
    title: t('StudyTable.title'),
    key: 'title',
  },
  {
    title: t('_global.status'),
    key: 'version_status',
  },
  {
    title: t('StudyTable.lts_version'),
    key: 'version_number',
  },
  {
    title: t('StudyTable.lts_locked_ver'),
    key: 'latest_locked_version',
  },
  {
    title: t('StudyTable.lts_released_ver'),
    key: 'latest_released_version',
  },
  {
    title: t('StudyTable.data_completeness'),
    key: 'data_completeness_tags',
  },
  {
    title: t('_global.modified'),
    key: 'version_start_date',
  },
  {
    title: t('_global.modified_by'),
    key: 'version_author',
  },
]

const activeStudiesTable = ref()
const deletedStudiesTable = ref()
const navigationTabs = ref()

const tabs = [
  { tab: 'active', name: t('SelectOrAddStudyTable.tab1_title') },
  { tab: 'deleted', name: t('SelectOrAddStudyTable.tab2_title') },
]

watch(searchString, () => {
  filterTable()
})

function reloadStudies() {
  cancelRead()
  fullRefresh.value = true
  filterTable()
}

function sort(data) {
  try {
    if (data[0]?.order === 'asc') {
      filteredStudies.value.sort((a, b) => {
        if (a[data[0].key] === null) return 1
        if (b[data[0].key] === null) return -1
        return a[data[0].key]
          .toString()
          .localeCompare(b[data[0].key].toString())
      })
    } else if (data[0]?.order === 'desc') {
      filteredStudies.value.sort((a, b) => {
        if (b[data[0].key] === null) return 1
        if (a[data[0].key] === null) return -1
        return b[data[0].key]
          .toString()
          .localeCompare(a[data[0].key].toString())
      })
    }
  } catch (error) {
    console.error(error)
  }
}

function cancelRead() {
  readVersion++
  pending?.controller.abort()
  pending = null
}
onBeforeUnmount(cancelRead)

function fetchActiveStudies(...args) {
  return fetchStudies('active', ...args)
}
function fetchDeletedStudies(...args) {
  return fetchStudies('deleted', ...args)
}

async function fetchStudies(panel, filters, options, filtersUpdated) {
  if (navigationTabs.value?.tab !== panel) return
  if (rowsFor !== panel) {
    cancelRead()
    rowsFor = panel
    rows.value = null
    paginatedStudies.value = []
  }
  const version = ++readVersion
  const params = filteringParameters.prepareParameters(
    options,
    savedFilters.value,
    filtersUpdated
  )
  studiesLoading.value = true
  loadError.value = ''
  try {
    if (!rows.value || fullRefresh.value) {
      if (!pending) {
        const controller = new AbortController()
        const query = panel === 'deleted' ? { deleted: true } : {}
        pending = { controller, read: api.getAllList(query, controller.signal) }
      }
      const { data } = await pending.read
      if (version !== readVersion || navigationTabs.value?.tab !== panel) return
      pending = null
      rows.value = data.map((study) => {
        const row = { ...study }
        for (const key of [
          'latest_locked_version',
          'latest_released_version',
        ]) {
          const version = row[key]
          if (version && typeof version === 'object')
            row[key] = [
              version.version_number,
              version.change_description || '',
            ].join(' ')
        }
        return row
      })
      fullRefresh.value = false
    }
    filteredStudies.value = rows.value
    handleFiltering(params)
  } catch (error) {
    if (version !== readVersion || navigationTabs.value?.tab !== panel) return
    pending = null
    rows.value = null
    paginatedStudies.value = []
    if (panel === 'active') totalActiveStudies.value = 0
    else totalDeletedStudies.value = 0
    loadError.value =
      error.response?.data?.message || t('_global.listing_read_failed')
  } finally {
    if (version === readVersion) studiesLoading.value = false
  }
}

function handleFiltering(params) {
  // Column filtering
  for (const key in columnFilters.value) {
    if (
      Array.isArray(columnFilters.value[key]) &&
      columnFilters.value[key].length === 0
    ) {
      delete columnFilters.value[key]
    }
  }

  const matchesFilterValue = (studyValue, filterValue) => {
    if (studyValue === null || studyValue === undefined) {
      return false
    }

    if (Array.isArray(studyValue)) {
      return studyValue.some((item) => matchesFilterValue(item, filterValue))
    }

    if (
      typeof studyValue === 'string' ||
      typeof filterValue === 'string' ||
      typeof studyValue === 'number' ||
      typeof filterValue === 'number'
    ) {
      const studyText = String(studyValue).toLowerCase()
      const filterText = String(filterValue).toLowerCase()
      return studyText.includes(filterText)
    }

    return studyValue === filterValue
  }

  for (let key in columnFilters.value) {
    const selectedValues = columnFilters.value[key]
    filteredStudies.value = filteredStudies.value.filter((study) => {
      return selectedValues.some((filterValue) =>
        matchesFilterValue(study[key], filterValue)
      )
    })
  }

  // Free text search
  let filteredTotal = 0
  if (searchString.value?.length >= 3) {
    filteredStudies.value = filteredStudies.value.filter((obj) =>
      Object.values(obj).some((value) =>
        String(value).toLowerCase().includes(searchString.value.toLowerCase())
      )
    )
  }

  // Pagination
  filteredTotal = filteredStudies.value.length
  paginatedStudies.value =
    params.page_size > 0
      ? filteredStudies.value.slice(
          (params.page_number - 1) * params.page_size,
          params.page_number * params.page_size
        )
      : filteredStudies.value

  if (navigationTabs.value.tab === 'active') {
    totalActiveStudies.value = filteredTotal
  } else {
    totalDeletedStudies.value = filteredTotal
  }
}

function getHeaderFilterData(key) {
  const source = rows.value || []

  const values = source.flatMap((obj) => {
    const value = obj[key]

    if (Array.isArray(value)) {
      if (value.length === 0) {
        return []
      }
      return value
        .filter((item) => item !== null && item !== undefined)
        .map((item) => String(item))
    }

    return value !== null && value !== undefined ? [value] : []
  })

  return [...new Set(values)].sort()
}

function changePanel(panel) {
  if (rowsFor !== panel) {
    cancelRead()
    rowsFor = panel
    rows.value = null
    filteredStudies.value = []
    paginatedStudies.value = []
  }
  clearFilters()
}

function clearFilters() {
  columnFilters.value = {}
  filterTable()
}

function filterTable() {
  if (navigationTabs.value?.tab === 'active') {
    activeStudiesTable.value?.filter()
  } else {
    deletedStudiesTable.value?.filter()
  }
}
</script>

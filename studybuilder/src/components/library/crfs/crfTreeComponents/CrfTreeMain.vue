<template>
  <v-row>
    <v-col>
      <v-select
        ref="mainSelect"
        v-model="selectCollection"
        :items="availableSelectCollections"
        :item-title="(item) => item?.name || $t('CRFTree.show_all')"
        :item-value="(item) => item?.uid || null"
        class="ms-4 mt-4"
        prepend-inner-icon="mdi-filter"
        hide-details
      >
        <template #prepend-item>
          <v-list-item style="cursor: pointer" @click="selectAll">
            <v-list-item-title class="ms-4">{{
              $t('CRFTree.show_all')
            }}</v-list-item-title>
          </v-list-item>
          <v-divider />
        </template>
      </v-select>
    </v-col>
    <v-spacer />
    <v-col class="d-flex justify-end me-4">
      <v-switch
        v-model="sortMode"
        class="ml-6 mt-4"
        :label="$t('CRFTree.reorder')"
        :disabled="expanded.length === 0"
      />
    </v-col>
  </v-row>
  <v-data-table-server
    v-model:expanded="expanded"
    :headers="headers"
    item-value="name"
    :items-length="totalCollections"
    :items="collections"
    :loading="loading"
    @update:options="getCollections"
  >
    <template #headers="{ columns }">
      <tr class="header">
        <template v-for="column in columns" :key="column.key">
          <td>
            <span>{{ column.title }}</span>
          </td>
        </template>
      </tr>
    </template>
    <template #item="{ item, internalItem, toggleExpand, isExpanded }">
      <tr style="background-color: rgb(var(--v-theme-dfltBackgroundLight1))">
        <td style="width: 45%" :class="'font-weight-bold'">
          <v-row class="align-center">
            <v-btn
              v-if="isExpanded(internalItem)"
              icon="mdi-chevron-down"
              variant="text"
              @click="doubleClick(toggleExpand, internalItem)"
            />
            <v-tooltip
              v-else-if="item.forms.length > 0"
              location="top left"
              :text="$t('CRFTree.double_click_expand')"
            >
              <template #activator="{ props }">
                <v-btn
                  v-bind="props"
                  icon="mdi-chevron-right"
                  variant="text"
                  @click="doubleClick(toggleExpand, internalItem)"
                />
              </template>
            </v-tooltip>
            <v-btn v-else variant="text" class="hide" icon />
            <ActionsMenu :actions="actions" :item="item" />
            <span class="ml-2">
              <v-icon color="crfCollection">mdi-alpha-c-circle</v-icon>
              <v-tooltip
                v-if="item.name.length > 60"
                location="top"
                max-width="300"
                :text="item.name"
                interactive
              >
                <template #activator="{ props }">
                  <span v-bind="props">{{
                    item.name.substring(0, 60) + '...'
                  }}</span>
                </template>
              </v-tooltip>
              <span v-else>{{ item.name }}</span>
            </span>
          </v-row>
        </td>
        <td style="width: 10%" />
        <td style="width: 10%" />
        <td style="width: 10%">
          <StatusChip :status="item.status" />
        </td>
        <td style="width: 10%">
          {{ item.version }}
        </td>
        <td style="width: 15%">
          <v-menu v-if="item.status !== statuses.FINAL" offset-y>
            <template #activator="{ props }">
              <div>
                <v-btn
                  v-show="item.status !== statuses.FINAL"
                  width="150px"
                  size="small"
                  rounded
                  v-bind="props"
                  color="crfForm"
                  :title="$t('CRFTree.link_forms')"
                >
                  <v-icon icon="mdi-plus" />
                  {{ $t('CRFTree.forms') }}
                </v-btn>
              </div>
            </template>
            <v-list>
              <v-list-item @click="openLinkForm(item)">
                <template #prepend>
                  <v-icon icon="mdi-plus" />
                </template>
                <v-list-item-title>
                  {{ $t('CRFTree.link_existing') }}
                </v-list-item-title>
              </v-list-item>
              <v-list-item @click="openCreateAndAddForm(item)">
                <template #prepend>
                  <v-icon icon="mdi-pencil-outline" />
                </template>
                <v-list-item-title>
                  {{ $t('CRFTree.create_and_link') }}
                </v-list-item-title>
              </v-list-item>
            </v-list>
          </v-menu>
        </td>
      </tr>
    </template>
    <template #expanded-row="{ columns, item }">
      <CrfTreeForms
        :sort-mode="sortMode"
        :parent-collection="item"
        :columns="columns"
        :refresh-forms="refreshForms"
        :expand-forms-for-collection="expandFormsForCollection"
        @update-parent-collection-form="updateCollectionForm"
      />
    </template>
  </v-data-table-server>
  <CrfCollectionForm
    :open="showCollectionForm"
    :selected-collection="selectedCollection"
    :read-only-prop="
      selectedCollection && selectedCollection.status === statuses.FINAL
    "
    @close="closeDefinition"
    @update-collection="updateCollection"
  />
  <CrfLinkForm
    :open="showLinkForm"
    :item-to-link="selectedCollection"
    items-type="forms"
    @close="closeLinkForm"
  />
  <v-dialog
    v-model="showExportForm"
    max-width="800px"
    persistent
    @keydown.esc="closeExportForm"
  >
    <CrfExportForm
      :item="selectedCollection"
      type="study_event"
      @close="closeExportForm"
    />
  </v-dialog>
  <v-dialog
    v-model="showCreateForm"
    persistent
    content-class="fullscreen-dialog"
  >
    <CrfFormForm
      class="fullscreen-dialog"
      @close="closeCreateAndAddForm"
      @link-form="linkForm"
    />
  </v-dialog>
  <CrfApprovalSummaryConfirmDialog ref="confirmApproval" />
  <ConfirmDialog ref="confirmNewVersion" />
</template>

<script setup>
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

import crfs from '@/api/crfs'
import CrfApprovalSummaryConfirmDialog from '@/components/library/crfs/CrfApprovalSummaryConfirmDialog.vue'
import CrfTreeForms from '@/components/library/crfs/crfTreeComponents/CrfTreeForms.vue'
import StatusChip from '@/components/tools/StatusChip.vue'
import statuses from '@/constants/statuses'
import CrfLinkForm from '@/components/library/crfs/CrfLinkForm.vue'
import ActionsMenu from '@/components/tools/ActionsMenu.vue'
import CrfCollectionForm from '@/components/library/crfs/CrfCollectionForm.vue'
import CrfExportForm from '@/components/library/crfs/CrfExportForm.vue'
import crfTypes from '@/constants/crfTypes'
import CrfFormForm from '@/components/library/crfs/CrfFormForm.vue'
import filteringParameters from '@/utils/filteringParameters'
import ConfirmDialog from '@/components/tools/ConfirmDialog.vue'
import isEmpty from 'lodash/isEmpty'

const notificationHub = inject('notificationHub')
const roles = inject('roles')
const router = useRouter()
const { t } = useI18n()

const mainSelect = ref(null)

const confirmApproval = ref(null)
const confirmNewVersion = ref(null)

const headers = computed(() => [
  { title: t('CRFTree.items_for_linking'), key: 'name' },
  { title: t('CRFTree.ref_attr'), key: 'refAttr' },
  { title: t('CRFTree.def_attr'), key: 'defAttr' },
  { title: t('_global.status'), key: 'status' },
  { title: t('_global.version'), key: 'version' },
  { title: t('CRFTree.link'), key: 'link' },
])

const showLinkForm = ref(false)
const showCollectionForm = ref(false)
const selectedCollection = ref({})
const refreshForms = ref(0)
const expanded = ref([])
const expandFormsForCollection = ref('')
const showExportForm = ref(false)
const showCreateForm = ref(false)
const sortMode = ref(false)
const collections = ref([])
const filteredOutCollections = ref([])
const totalCollections = ref(0)
const loading = ref(false)
const selectCollection = ref({})
const doubleClickCounter = ref(0)
const doubleClickTimer = ref(null)

const actions = computed(() => [
  {
    label: t('CRFTree.open_def'),
    icon: 'mdi-eye-outline',
    click: openDefinition,
  },
  {
    label: t('CRFTree.preview_odm'),
    icon: 'mdi-file-xml-box',
    click: previewODM,
  },
  {
    label: t('_global.new_version'),
    icon: 'mdi-plus-circle-outline',
    click: newVersion,
    condition: (item) => item.status === statuses.FINAL,
    accessRole: roles.LIBRARY_WRITE,
  },
  {
    label: t('_global.approve'),
    icon: 'mdi-check-decagram',
    click: approve,
    condition: (item) => item.status === statuses.DRAFT,
    accessRole: roles.LIBRARY_WRITE,
  },
  {
    label: t('CRFTree.create_new_version_all'),
    icon: 'mdi-plus-circle-outline',
    click: newVersionAll,
    condition: (item) => item.status === statuses.FINAL,
    accessRole: roles.LIBRARY_WRITE,
  },
  {
    label: t('_global.export'),
    icon: 'mdi-download-outline',
    click: openExportForm,
  },
  {
    label: t('CRFTree.expand'),
    icon: 'mdi-arrow-expand-down',
    condition: (item) => item.forms.length > 0,
    click: expandAll,
  },
])

const availableSelectCollections = computed(() => {
  return [...collections.value, ...filteredOutCollections.value].sort((a, b) =>
    a.name.localeCompare(b.name)
  )
})

watch(selectCollection, (val) => {
  updateCollectionView(val)
})

onMounted(() => {
  getCollections()
})

onBeforeUnmount(() => {
  if (doubleClickTimer.value) {
    clearTimeout(doubleClickTimer.value)
  }
})

function doubleClick(toggleExpand, item) {
  doubleClickCounter.value++

  if (doubleClickCounter.value === 1) {
    doubleClickTimer.value = setTimeout(() => {
      toggleExpand(item)
      doubleClickCounter.value = 0
      expandFormsForCollection.value = ''
    }, 500)
  } else {
    expandAll(item.raw)
    clearTimeout(doubleClickTimer.value)
    doubleClickCounter.value = 0
  }
}

function updateCollectionView(collection) {
  if (isEmpty(collection)) {
    collections.value = [...collections.value, ...filteredOutCollections.value]
    filteredOutCollections.value = []
    return
  }

  const allCollections = [...collections.value, ...filteredOutCollections.value]
  collections.value = allCollections.filter((c) => c.uid === collection)
  filteredOutCollections.value = allCollections.filter(
    (c) => c.uid !== collection
  )
}

async function newVersion(item) {
  expanded.value = expanded.value.filter((e) => e !== item.name)

  if (
    await confirmNewVersion.value?.open(
      t('_global.continuation_confirmation'),
      {
        type: 'warning',
      }
    )
  ) {
    loading.value = true

    crfs
      .newVersion('study-events', item.uid)
      .then((resp) => {
        collections.value = collections.value.map((c) =>
          c.uid === resp.data.uid ? { ...c, ...resp.data } : c
        )

        expandAll(item)
        notificationHub?.add({
          msg: t('_global.new_version_success'),
        })
      })
      .finally(() => {
        loading.value = false
      })
  }
}

function updateCollectionForm(affectedCollection, updatedForm) {
  if (affectedCollection.status == statuses.DRAFT) {
    const collection = collections.value.find(
      (c) => c.uid === affectedCollection.uid
    )
    if (collection) {
      collection.forms = collection.forms.map((f) =>
        f.uid === updatedForm.uid ? { ...f, ...updatedForm } : f
      )
    }
  }
}

function selectAll() {
  selectCollection.value = {}
  mainSelect.value?.blur()
}

async function getCollections(options) {
  options = { ...options, sortBy: [{ key: 'name', order: 'asc' }] }
  const params = filteringParameters.prepareParameters(options, null, null)
  if (!params) {
    params.total_count = true
  }

  return crfs.get('study-events', { params }).then((resp) => {
    collections.value = resp.data.items
    updateCollectionView(selectCollection.value)
    totalCollections.value = resp.data.total
  })
}

function openCreateAndAddForm(item) {
  selectedCollection.value = item
  showCreateForm.value = true
}

function closeCreateAndAddForm() {
  showCreateForm.value = false
  selectedCollection.value = {}
}

function linkForm(form) {
  const payload = [
    {
      uid: form.data.uid,
      order_number: selectedCollection.value.forms.length,
      mandatory: 'No',
      collection_exception_condition_oid: null,
    },
  ]

  crfs
    .addFormsToCollection(payload, selectedCollection.value.uid, false)
    .then(() => {
      getCollections().then(() => {
        refreshForms.value += 1
      })
    })
}

function updateCollection(collection) {
  collections.value = collections.value.map((c) =>
    c.uid === collection.uid ? { ...c, ...collection } : c
  )
}

function openDefinition(item) {
  selectedCollection.value = item
  showCollectionForm.value = true
}

function closeDefinition() {
  selectedCollection.value = {}
  showCollectionForm.value = false
  getCollections()
}

function openLinkForm(item) {
  selectedCollection.value = item
  showLinkForm.value = true
}

async function closeLinkForm() {
  showLinkForm.value = false
  selectedCollection.value = {}
  await getCollections()
  refreshForms.value += 1
}

async function expandAll(item) {
  await expanded.value.push(item.name)
  expandFormsForCollection.value = item.uid
}

function openExportForm(item) {
  selectedCollection.value = item
  showExportForm.value = true
}

function closeExportForm() {
  selectedCollection.value = {}
  showExportForm.value = false
}

function previewODM(item) {
  router.push({
    name: 'CrfBuilder',
    params: {
      tab: 'odm-viewer',
      uid: item.uid,
      type: crfTypes.STUDY_EVENT,
    },
  })
}

async function approve(item) {
  expanded.value = expanded.value.filter((e) => e !== item.name)

  if (
    item.status === statuses.DRAFT &&
    (await confirmApproval.value?.open({
      agreeLabel: t('CRFCollections.approve_collection'),
      collection: item,
    }))
  ) {
    loading.value = true

    await crfs
      .approve('study-events', item.uid)
      .then((resp) => {
        collections.value = collections.value.map((collection) => {
          if (collection.uid === resp.data.uid) {
            return { ...collection, ...resp.data }
          }
          return collection
        })

        notificationHub?.add({
          msg: t('CRFCollections.approved'),
        })
        expandAll(item)
      })
      .finally(() => {
        loading.value = false
      })
  }
}

async function newVersionAll(item) {
  expanded.value = expanded.value.filter((e) => e !== item.name)

  if (
    item.status === statuses.FINAL &&
    (await confirmNewVersion.value?.open(
      t('CRFTree.new_version_affecting_children_warning'),
      {
        agreeLabel: t('CRFTree.create_new_versions'),
        type: 'warning',
      }
    ))
  ) {
    loading.value = true

    await crfs
      .newVersion('study-events', item.uid, {
        params: { cascade_new_version: true },
      })
      .then((resp) => {
        collections.value = collections.value.map((collection) => {
          if (collection.uid === resp.data.uid) {
            return { ...collection, ...resp.data }
          }
          return collection
        })

        notificationHub?.add({
          msg: t('CRFCollections.new_version_all'),
        })
        expandAll(item)
      })
      .finally(() => {
        loading.value = false
      })
  }
}
</script>
<style scoped>
.hide {
  opacity: 0;
  cursor: default;
}
.header {
  background-color: rgb(var(--v-theme-tableGray)) !important;
  color: rgba(26, 26, 26, 0.6) !important;
  text-align: start;
  font-weight: 500;
  font-size: 14px;
}
</style>

<template>
  <td :colspan="columns.length" class="pa-0">
    <v-data-table
      id="itemGroups"
      v-model:expanded="expanded"
      :initial-sort-by="[{ key: 'order_number', order: 'asc' }]"
      :headers="columns"
      :items="itemGroups"
      item-value="name"
      light
      :loading="loading"
      :items-per-page="-1"
    >
      <template #headers="{ columns }">
        <tr>
          <template v-for="column in columns" :key="column.key">
            <td>
              <span>{{ column.title }}</span>
            </td>
          </template>
        </tr>
      </template>
      <template #bottom />
      <template #item="{ item, internalItem, toggleExpand, isExpanded, index }">
        <tr style="background-color: lightgrey">
          <td style="width: 45%" :class="'font-weight-bold'">
            <v-row class="align-center">
              <v-btn
                v-if="isExpanded(internalItem)"
                icon="mdi-chevron-down"
                variant="text"
                class="ml-8"
                @click="toggleExpand(internalItem)"
              />
              <v-btn
                v-else-if="item.items && item.items.length > 0"
                icon="mdi-chevron-right"
                variant="text"
                class="ml-8"
                @click="toggleExpand(internalItem)"
              />
              <v-btn v-else variant="text" class="ml-8 hide" icon />
              <CrfTreeReorderButtons
                :sort-mode="sortMode"
                :is-parent-draft="parentForm.status === statuses.DRAFT"
                :sibling-length="parentForm.item_groups.length"
                :item="item"
                :index="index"
                @order-up="orderUp"
                @order-down="orderDown"
              />
              <ActionsMenu :actions="actions" :item="item" />
              <span class="ml-2">
                <v-icon color="crfGroup"> mdi-alpha-g-circle </v-icon>
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
          <td style="width: 10%">
            <CrfTreeTooltipsHandler :item="item" value="mandatory" />
            <CrfTreeTooltipsHandler :item="item" value="locked" />
            <CrfTreeTooltipsHandler :item="item" value="refAttrs" />
          </td>
          <td style="width: 10%">
            <CrfTreeTooltipsHandler :item="item" value="repeating" />
            <CrfTreeTooltipsHandler :item="item" value="is_reference_data" />
            <CrfTreeTooltipsHandler :item="item" value="vendor" />
          </td>
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
                    color="crfItem"
                    :title="$t('CRFTree.link_items')"
                  >
                    <v-icon icon="mdi-plus" />
                    {{ $t('CRFTree.items') }}
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
        <CrfTreeItems
          :sort-mode="sortMode"
          :parent-item-group="item"
          :parent-form="parentForm"
          :columns="columns"
          @update-parent-item-group-item="updateItemGroupItem"
        />
      </template>
    </v-data-table>
  </td>
  <v-dialog
    v-model="showItemGroupForm"
    persistent
    content-class="fullscreen-dialog"
  >
    <CrfItemGroupForm
      :selected-group="selectedItemGroup"
      :read-only-prop="
        selectedItemGroup && selectedItemGroup.status === statuses.FINAL
      "
      class="fullscreen-dialog"
      @close="closeDefinition"
      @update-item-group="updateItemGroup"
      @link-group="linkGroup"
    />
  </v-dialog>
  <CrfLinkForm
    :open="showLinkForm"
    :item-to-link="selectedItemGroup"
    items-type="items"
    @close="closeLinkForm"
  />
  <v-dialog
    v-model="showExportForm"
    max-width="800px"
    persistent
    @keydown.esc="closeExportForm"
  >
    <CrfExportForm
      :item="selectedItemGroup"
      type="item_group"
      @close="closeExportForm"
    />
  </v-dialog>
  <CrfReferencesForm
    :open="showAttributesForm"
    :parent="parentForm"
    :element="selectedItemGroup"
    :read-only="selectedItemGroup.status === statuses.FINAL"
    @close="closeAttributesForm"
  />
  <v-dialog
    v-model="showCreateForm"
    persistent
    content-class="fullscreen-dialog"
  >
    <CrfItemForm
      class="fullscreen-dialog"
      @close="closeCreateAndAddForm"
      @link-item="linkItem"
    />
  </v-dialog>
  <CrfApprovalSummaryConfirmDialog ref="confirmApproval" />
  <CrfNewVersionSummaryConfirmDialog ref="confirmNewVersion" />
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

import crfs from '@/api/crfs'
import CrfTreeItems from '@/components/library/crfs/crfTreeComponents/CrfTreeItems.vue'
import CrfTreeTooltipsHandler from '@/components/library/crfs/CrfTreeTooltipsHandler.vue'
import StatusChip from '@/components/tools/StatusChip.vue'
import statuses from '@/constants/statuses'
import CrfLinkForm from '@/components/library/crfs/CrfLinkForm.vue'
import ActionsMenu from '@/components/tools/ActionsMenu.vue'
import CrfItemGroupForm from '@/components/library/crfs/CrfItemGroupForm.vue'
import _isEmpty from 'lodash/isEmpty'
import CrfExportForm from '@/components/library/crfs/CrfExportForm.vue'
import CrfReferencesForm from '@/components/library/crfs/CrfReferencesForm.vue'
import crfTypes from '@/constants/crfTypes'
import CrfItemForm from '@/components/library/crfs/CrfItemForm.vue'
import parameters from '@/constants/parameters'
import CrfTreeReorderButtons from '@/components/library/crfs/CrfTreeReorderButtons.vue'
import CrfApprovalSummaryConfirmDialog from '@/components/library/crfs/CrfApprovalSummaryConfirmDialog.vue'
import CrfNewVersionSummaryConfirmDialog from '@/components/library/crfs/CrfNewVersionSummaryConfirmDialog.vue'

const props = defineProps({
  parentForm: {
    type: Object,
    default: null,
  },
  columns: {
    type: Array,
    default: null,
  },
  refreshItemGroups: {
    type: Number,
    default: null,
  },
  expandGroupsForForm: {
    type: Array,
    default: null,
  },
  sortMode: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['updateParentFormItemGroup'])

const notificationHub = inject('notificationHub')
const roles = inject('roles')
const router = useRouter()
const { t } = useI18n()

const itemGroups = ref([])
const loading = ref(false)
const selectedItemGroup = ref({})
const showItemGroupForm = ref(false)
const showCreateForm = ref(false)
const showLinkForm = ref(false)
const showExportForm = ref(false)
const showAttributesForm = ref(false)
const expanded = ref([])

const refreshItems = ref(0)

const confirmApproval = ref(null)
const confirmNewVersion = ref(null)

const actions = computed(() => [
  {
    label: t('CRFTree.open_def'),
    icon: 'mdi-eye-outline',
    click: openDefinition,
  },
  {
    label: t('CRFTree.edit_reference'),
    icon: 'mdi-pencil-outline',
    click: editAttributes,
    condition: (item) => item.status === statuses.DRAFT,
    accessRole: roles.LIBRARY_WRITE,
  },
  {
    label: t('CRFTree.preview_odm'),
    icon: 'mdi-file-xml-box',
    click: previewODM,
  },
  {
    label: t('_global.approve'),
    icon: 'mdi-check-decagram',
    click: approve,
    condition: (item) => item.status === statuses.DRAFT,
    accessRole: roles.LIBRARY_WRITE,
  },
  {
    label: t('_global.new_version'),
    icon: 'mdi-plus-circle-outline',
    click: newVersion,
    condition: (item) => item.status === statuses.FINAL,
    accessRole: roles.LIBRARY_WRITE,
  },
  {
    label: t('_global.export'),
    icon: 'mdi-download-outline',
    click: openExportForm,
  },
])

watch(
  () => props.refreshItemGroups,
  () => {
    fetchItemGroups()
  }
)

watch(
  () => props.expandGroupsForForm,
  (value) => {
    if (!_isEmpty(value) && value.includes(props.parentForm?.uid)) {
      expanded.value = itemGroups.value
        .map((group) => (group.items.length > 0 ? group.name : null))
        .filter((val) => val !== null)
    }
  }
)

onMounted(() => {
  fetchItemGroups()
})

async function newVersion(item) {
  expanded.value = expanded.value.filter((e) => e !== item.name)

  if (
    await confirmNewVersion.value?.open({
      agreeLabel: t('CRFItemGroups.create_new_version'),
      itemGroup: item,
    })
  ) {
    loading.value = true

    crfs
      .newVersion('item-groups', item.uid)
      .then((resp) => {
        if (props.parentForm?.status === statuses.DRAFT) {
          updateItemGroup(resp.data)
        }

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

async function approve(item) {
  expanded.value = expanded.value.filter((e) => e !== item.name)

  if (
    await confirmApproval.value?.open({
      agreeLabel: t('CRFItemGroups.approve_group'),
      itemGroup: item,
    })
  ) {
    loading.value = true

    crfs
      .approve('item-groups', item.uid)
      .then((resp) => {
        updateItemGroup(resp.data)

        expandAll(item)
        notificationHub?.add({
          msg: t('CRFItemGroups.approved'),
        })
      })
      .finally(() => {
        loading.value = false
      })
  }
}

function updateItemGroupItem(affectedItemGroup, updatedItem) {
  if (affectedItemGroup.status == statuses.DRAFT) {
    const itemGroup = itemGroups.value.find(
      (ig) => ig.uid === affectedItemGroup.uid
    )
    if (itemGroup) {
      itemGroup.items = itemGroup.items.map((i) =>
        i.uid === updatedItem.uid ? { ...i, ...updatedItem } : i
      )
    }
  }
}

function openCreateAndAddForm(item) {
  selectedItemGroup.value = item
  showCreateForm.value = true
}

function closeCreateAndAddForm() {
  showCreateForm.value = false
  selectedItemGroup.value = {}
}

function linkItem(item) {
  const payload = [
    {
      uid: item.data.uid,
      order_number: selectedItemGroup.value.items.length,
      mandatory: 'No',
      collection_exception_condition_oid: null,
      key_sequence: parameters.NULL,
      methodOid: parameters.NULL,
      imputation_method_oid: parameters.NULL,
      role: parameters.NULL,
      role_codelist_oid: parameters.NULL,
      data_entry_required: 'No',
      sdv: 'No',
      vendor: { attributes: [] },
    },
  ]

  crfs
    .addItemsToItemGroup(payload, selectedItemGroup.value.uid, false)
    .then(() => {
      fetchItemGroups()
    })
}

async function fetchItemGroups() {
  if (!props.parentForm) {
    return
  }

  loading.value = true
  itemGroups.value = []
  for (const itemGroup of props.parentForm.item_groups ?? []) {
    const rs = await crfs.get(`item-groups/${itemGroup.uid}`, {
      params: { version: itemGroup.version },
    })
    itemGroups.value.push({ ...itemGroup, ...rs.data })
  }

  refreshItems.value += 1
  loading.value = false

  if (
    !_isEmpty(props.expandGroupsForForm) &&
    props.expandGroupsForForm.includes(props.parentForm.uid)
  ) {
    expanded.value = itemGroups.value
      .map((group) => (group.items.length > 0 ? group.name : null))
      .filter((val) => val !== null)
  }
}

function updateItemGroup(itemGroup) {
  itemGroups.value = itemGroups.value.map((ig) =>
    ig.uid === itemGroup.uid ? { ...ig, ...itemGroup } : ig
  )
  emit('updateParentFormItemGroup', props.parentForm, itemGroup)
}

function openDefinition(item) {
  selectedItemGroup.value = item
  showItemGroupForm.value = true
}

function closeDefinition() {
  selectedItemGroup.value = {}
  showItemGroupForm.value = false
  fetchItemGroups()
}

function openLinkForm(item) {
  selectedItemGroup.value = item
  showLinkForm.value = true
}

function closeLinkForm() {
  showLinkForm.value = false
  selectedItemGroup.value = {}
  fetchItemGroups()
}

async function expandAll(item) {
  await expanded.value.push(item.name)
}

function openExportForm(item) {
  selectedItemGroup.value = item
  showExportForm.value = true
}

function closeExportForm() {
  selectedItemGroup.value = {}
  showExportForm.value = false
}

function editAttributes(item) {
  selectedItemGroup.value = item
  showAttributesForm.value = true
}

function closeAttributesForm() {
  selectedItemGroup.value = {}
  showAttributesForm.value = false
}

function previewODM(item) {
  router.push({
    name: 'CrfBuilder',
    params: {
      tab: 'odm-viewer',
      uid: item.uid,
      type: crfTypes.ITEM_GROUP,
    },
  })
}

function orderUp(item, index) {
  if (index === 0) {
    return
  }

  itemGroups.value[index].order_number--
  itemGroups.value[index - 1].order_number++
  itemGroups.value.sort((a, b) => a.order_number - b.order_number)
  crfs.addItemGroupsToForm(itemGroups.value, props.parentForm.uid, true)
}

function orderDown(item, index) {
  if (index === itemGroups.value.length - 1) {
    return
  }

  itemGroups.value[index].order_number++
  itemGroups.value[index + 1].order_number--
  itemGroups.value.sort((a, b) => a.order_number - b.order_number)
  crfs.addItemGroupsToForm(itemGroups.value, props.parentForm.uid, true)
}
</script>
<style scoped>
#forms .v-table__wrapper > table > thead > tr {
  visibility: collapse;
}
</style>

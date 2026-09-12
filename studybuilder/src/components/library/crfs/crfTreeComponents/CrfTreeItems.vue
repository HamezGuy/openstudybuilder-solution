<template>
  <td :colspan="columns.length" class="pa-0">
    <v-data-table
      id="items"
      :initial-sort-by="[{ key: 'order_number', order: 'asc' }]"
      :headers="columns"
      :items="items"
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
      <template #item="{ item, index }">
        <tr style="background-color: var(--v-dfltBackground-base)">
          <td style="width: 45%" :class="'font-weight-bold'">
            <v-row class="align-center">
              <v-btn variant="text" icon class="ml-12 hide" />
              <CrfTreeReorderButtons
                :sort-mode="sortMode"
                :is-parent-draft="parentItemGroup.status === statuses.DRAFT"
                :sibling-length="parentItemGroup.items.length"
                :item="item"
                :index="index"
                @order-up="orderUp"
                @order-down="orderDown"
              />
              <ActionsMenu :actions="actions" :item="item" />
              <span class="ml-2">
                <v-icon color="crfItem"> mdi-alpha-i-circle </v-icon>
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
            <CrfTreeTooltipsHandler :item="item" value="dataType" />
            <CrfTreeTooltipsHandler :item="item" value="vendor" />
          </td>
          <td style="width: 10%">
            <StatusChip :status="item.status" />
          </td>
          <td style="width: 10%">
            {{ item.version }}
          </td>
          <td></td>
        </tr>
      </template>
    </v-data-table>
  </td>
  <v-dialog v-model="showItemForm" persistent content-class="fullscreen-dialog">
    <CrfItemForm
      :selected-item="selectedItem"
      :start-step="startStep"
      :form-view="false"
      :read-only-prop="selectedItem && selectedItem.status === statuses.FINAL"
      class="fullscreen-dialog"
      @close="closeDefinition"
      @update-item="updateItem"
      @link-item="linkItem"
    />
  </v-dialog>
  <v-dialog
    v-model="showExportForm"
    max-width="800px"
    persistent
    @keydown.esc="closeExportForm"
  >
    <CrfExportForm :item="selectedItem" type="item" @close="closeExportForm" />
  </v-dialog>
  <CrfReferencesForm
    :open="showAttributesForm"
    :parent="parentItemGroup"
    :element="selectedItem"
    :read-only="selectedItem.status === statuses.FINAL"
    @close="closeAttributesForm"
  />
  <CrfNewVersionSummaryConfirmDialog ref="confirmNewVersion" />
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

import crfs from '@/api/crfs'
import CrfTreeTooltipsHandler from '@/components/library/crfs/CrfTreeTooltipsHandler.vue'
import StatusChip from '@/components/tools/StatusChip.vue'
import statuses from '@/constants/statuses'
import ActionsMenu from '@/components/tools/ActionsMenu.vue'
import CrfItemForm from '@/components/library/crfs/CrfItemForm.vue'
import CrfExportForm from '@/components/library/crfs/CrfExportForm.vue'
import CrfReferencesForm from '@/components/library/crfs/CrfReferencesForm.vue'
import CrfTreeReorderButtons from '@/components/library/crfs/CrfTreeReorderButtons.vue'
import crfTypes from '@/constants/crfTypes'
import CrfNewVersionSummaryConfirmDialog from '@/components/library/crfs/CrfNewVersionSummaryConfirmDialog.vue'

const props = defineProps({
  parentItemGroup: {
    type: Object,
    default: null,
  },
  parentForm: {
    type: Object,
    default: null,
  },
  columns: {
    type: Array,
    default: null,
  },
  refreshItems: {
    type: Number,
    default: null,
  },
  sortMode: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['updateParentItemGroupItem'])

const notificationHub = inject('notificationHub')
const roles = inject('roles')
const router = useRouter()
const { t } = useI18n()

const items = ref([])
const loading = ref(false)
const selectedItem = ref({})
const startStep = ref(null)
const showItemForm = ref(false)
const showExportForm = ref(false)
const showAttributesForm = ref(false)

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
    label: t('CRFTree.manage_activity_instances'),
    icon: 'mdi-link',
    click: openActivityInstanceTable,
    condition: (item) => item.status === statuses.DRAFT,
    accessRole: roles.LIBRARY_READ,
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
  () => props.refreshItems,
  () => {
    fetchItems()
  }
)

onMounted(() => {
  fetchItems()
})

async function newVersion(item) {
  if (
    await confirmNewVersion.value?.open({
      agreeLabel: t('CRFItems.create_new_version'),
      item: item,
    })
  ) {
    loading.value = true

    crfs
      .newVersion('items', item.uid)
      .then((resp) => {
        if (props.parentItemGroup?.status === statuses.DRAFT) {
          updateItem(resp.data)
        }

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
  loading.value = true

  crfs
    .approve('items', item.uid)
    .then((resp) => {
      updateItem(resp.data)

      notificationHub?.add({
        msg: t('CRFItems.approved'),
      })
    })
    .finally(() => {
      loading.value = false
    })
}

async function fetchItems() {
  if (!props.parentItemGroup) {
    return
  }

  loading.value = true
  items.value = []
  for (const item of props.parentItemGroup.items ?? []) {
    const rs = await crfs.get(`items/${item.uid}`, {
      params: { version: item.version },
    })
    items.value.push({ ...item, ...rs.data })
  }
  loading.value = false
}

function updateItem(item) {
  items.value = items.value.map((i) =>
    i.uid === item.uid ? { ...i, ...item } : i
  )
  emit('updateParentItemGroupItem', props.parentItemGroup, item)
}

function openDefinition(item) {
  selectedItem.value = item
  showItemForm.value = true
}

function closeDefinition() {
  selectedItem.value = {}
  startStep.value = null
  showItemForm.value = false
  fetchItems()
}

function openExportForm(item) {
  selectedItem.value = item
  showExportForm.value = true
}

function closeExportForm() {
  selectedItem.value = {}
  showExportForm.value = false
}

function editAttributes(item) {
  selectedItem.value = item
  showAttributesForm.value = true
}

function openActivityInstanceTable(item) {
  selectedItem.value = item
  startStep.value = 'activity_instance_links'
  showItemForm.value = true
}

function closeAttributesForm() {
  selectedItem.value = {}
  showAttributesForm.value = false
}

function previewODM(item) {
  router.push({
    name: 'CrfBuilder',
    params: {
      tab: 'odm-viewer',
      uid: item.uid,
      type: crfTypes.ITEM,
    },
  })
}

function orderUp(item, index) {
  if (index === 0) {
    return
  }

  items.value[index].order_number--
  items.value[index - 1].order_number++
  items.value.sort((a, b) => a.order_number - b.order_number)
  crfs.addItemsToItemGroup(items.value, props.parentItemGroup.uid, true)
}

function orderDown(item, index) {
  if (index === items.value.length - 1) {
    return
  }

  items.value[index].order_number++
  items.value[index + 1].order_number--
  items.value.sort((a, b) => a.order_number - b.order_number)
  crfs.addItemsToItemGroup(items.value, props.parentItemGroup.uid, true)
}
</script>
<style scoped>
#forms .v-table__wrapper > table > thead > tr {
  visibility: collapse;
}
</style>

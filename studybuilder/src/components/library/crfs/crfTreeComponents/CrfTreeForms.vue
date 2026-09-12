<template>
  <td :colspan="columns.length" class="pa-0">
    <v-data-table
      id="forms"
      v-model:expanded="expanded"
      :initial-sort-by="[{ key: 'order_number', order: 'asc' }]"
      :headers="columns"
      :items="forms"
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
        <tr style="background-color: rgb(var(--v-theme-dfltBackgroundLight2))">
          <td style="width: 45%" :class="'font-weight-bold'">
            <v-row class="align-center">
              <v-btn
                v-if="isExpanded(internalItem)"
                icon="mdi-chevron-down"
                variant="text"
                class="ml-4"
                @click="toggleExpand(internalItem)"
              />
              <v-btn
                v-else-if="item.item_groups && item.item_groups.length > 0"
                icon="mdi-chevron-right"
                variant="text"
                class="ml-4"
                @click="toggleExpand(internalItem)"
              />
              <v-btn v-else variant="text" class="ml-4 hide" icon />
              <CrfTreeReorderButtons
                :sort-mode="sortMode"
                :is-parent-draft="parentCollection.status === statuses.DRAFT"
                :sibling-length="parentCollection.forms.length"
                :item="item"
                :index="index"
                @order-up="orderUp"
                @order-down="orderDown"
              />
              <ActionsMenu :actions="actions" :item="item" />
              <span class="ml-2">
                <v-icon color="crfForm"> mdi-alpha-f-circle </v-icon>
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
          </td>
          <td style="width: 10%">
            <CrfTreeTooltipsHandler :item="item" value="repeating" />
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
                    color="crfGroup"
                    :title="$t('CRFTree.link_item_groups')"
                  >
                    <v-icon icon="mdi-plus" />
                    {{ $t('CRFTree.item_groups') }}
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
        <CrfTreeItemGroups
          :sort-mode="sortMode"
          :parent-form="item"
          :columns="columns"
          :refresh-item-groups="refreshItemGroups"
          :expand-groups-for-form="expandGroupsForForm"
          @update-parent-form-item-group="updateFormItemGroup"
        />
      </template>
    </v-data-table>
  </td>
  <v-dialog v-model="showFormForm" persistent content-class="fullscreen-dialog">
    <CrfFormForm
      :selected-form="selectedForm"
      :read-only-prop="selectedForm && selectedForm.status === statuses.FINAL"
      class="fullscreen-dialog"
      @close="closeDefinition"
      @link-form="linkForm"
      @update-form="updateForm"
    />
  </v-dialog>
  <CrfLinkForm
    :open="showLinkForm"
    :item-to-link="selectedForm"
    items-type="item-groups"
    @close="closeLinkForm"
  />
  <v-dialog
    v-model="showExportForm"
    max-width="800px"
    persistent
    @keydown.esc="closeExportForm"
  >
    <CrfExportForm :item="selectedForm" type="form" @close="closeExportForm" />
  </v-dialog>
  <CrfReferencesForm
    :open="showAttributesForm"
    :parent="parentCollection"
    :element="selectedForm"
    :read-only="selectedForm.status === statuses.FINAL"
    @close="closeAttributesForm"
  />
  <v-dialog
    v-model="showCreateForm"
    persistent
    content-class="fullscreen-dialog"
  >
    <CrfItemGroupForm
      class="fullscreen-dialog"
      @close="closeCreateAndAddForm"
      @link-group="linkItemGroup"
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
import CrfTreeItemGroups from '@/components/library/crfs/crfTreeComponents/CrfTreeItemGroups.vue'
import CrfTreeTooltipsHandler from '@/components/library/crfs/CrfTreeTooltipsHandler.vue'
import StatusChip from '@/components/tools/StatusChip.vue'
import statuses from '@/constants/statuses'
import CrfLinkForm from '@/components/library/crfs/CrfLinkForm.vue'
import ActionsMenu from '@/components/tools/ActionsMenu.vue'
import CrfFormForm from '@/components/library/crfs/CrfFormForm.vue'
import _isEmpty from 'lodash/isEmpty'
import CrfExportForm from '@/components/library/crfs/CrfExportForm.vue'
import CrfReferencesForm from '@/components/library/crfs/CrfReferencesForm.vue'
import crfTypes from '@/constants/crfTypes'
import CrfItemGroupForm from '@/components/library/crfs/CrfItemGroupForm.vue'
import CrfTreeReorderButtons from '@/components/library/crfs/CrfTreeReorderButtons.vue'
import CrfApprovalSummaryConfirmDialog from '@/components/library/crfs/CrfApprovalSummaryConfirmDialog.vue'
import CrfNewVersionSummaryConfirmDialog from '@/components/library/crfs/CrfNewVersionSummaryConfirmDialog.vue'

const props = defineProps({
  parentCollection: {
    type: Object,
    default: null,
  },
  columns: {
    type: Array,
    default: null,
  },
  refreshForms: {
    type: Number,
    default: null,
  },
  expandFormsForCollection: {
    type: String,
    default: null,
  },
  sortMode: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['updateParentCollectionForm'])

const notificationHub = inject('notificationHub')
const roles = inject('roles')
const router = useRouter()
const { t } = useI18n()

const forms = ref([])
const loading = ref(false)
const showFormForm = ref(false)
const showLinkForm = ref(false)
const selectedForm = ref({})
const refreshItemGroups = ref(0)
const expanded = ref([])
const expandGroupsForForm = ref([])
const showExportForm = ref(false)
const showAttributesForm = ref(false)
const showCreateForm = ref(false)

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
  {
    label: t('CRFTree.expand'),
    icon: 'mdi-arrow-expand-down',
    condition: (item) => item.item_groups.length > 0,
    click: expandAll,
  },
])

watch(
  () => props.refreshForms,
  () => {
    fetchForms()
  }
)

watch(
  () => props.expandFormsForCollection,
  (value) => {
    if (!_isEmpty(value) && value === props.parentCollection?.uid) {
      expanded.value = forms.value
        .map((form) => (form.item_groups.length > 0 ? form.name : null))
        .filter((val) => val !== null)

      expandGroupsForForm.value = forms.value
        .map((form) => (form.item_groups.length > 0 ? form.uid : null))
        .filter((val) => val !== null)
    }
  }
)

onMounted(() => {
  fetchForms()
})

async function newVersion(item) {
  expanded.value = expanded.value.filter((e) => e !== item.name)

  if (
    await confirmNewVersion.value?.open({
      agreeLabel: t('CRFForms.create_new_version'),
      form: item,
    })
  ) {
    loading.value = true

    crfs
      .newVersion('forms', item.uid)
      .then((resp) => {
        if (props.parentCollection?.status === statuses.DRAFT) {
          updateForm(resp.data)
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
      agreeLabel: t('CRFForms.approve_form'),
      form: item,
    })
  ) {
    loading.value = true

    crfs
      .approve('forms', item.uid)
      .then((resp) => {
        updateForm(resp.data)

        expandAll(item)
        notificationHub?.add({
          msg: t('CRFForms.approved'),
        })
      })
      .finally(() => {
        loading.value = false
      })
  }
}

function updateFormItemGroup(affectedForm, updatedItemGroup) {
  if (affectedForm.status === statuses.DRAFT) {
    const form = forms.value.find((f) => f.uid === affectedForm.uid)

    if (form) {
      form.item_groups = form.item_groups.map((f) =>
        f.uid === updatedItemGroup.uid ? { ...f, ...updatedItemGroup } : f
      )
    }
  }
}

function openCreateAndAddForm(item) {
  selectedForm.value = item
  showCreateForm.value = true
}

function closeCreateAndAddForm() {
  showCreateForm.value = false
  selectedForm.value = {}
}

function linkItemGroup(group) {
  const payload = [
    {
      uid: group.data.uid,
      order_number: selectedForm.value.item_groups.length,
      mandatory: 'No',
      collection_exception_condition_oid: null,
      vendor: { attributes: [] },
    },
  ]

  crfs.addItemGroupsToForm(payload, selectedForm.value.uid, false).then(() => {
    fetchForms()
  })
}

async function fetchForms() {
  if (!props.parentCollection) {
    return
  }

  loading.value = true
  forms.value = []
  for (const form of props.parentCollection.forms ?? []) {
    const rs = await crfs.get(`forms/${form.uid}`, {
      params: { version: form.version },
    })
    forms.value.push({ ...form, ...rs.data })
  }

  refreshItemGroups.value += 1
  loading.value = false

  if (
    !_isEmpty(props.expandFormsForCollection) &&
    props.expandFormsForCollection === props.parentCollection.uid
  ) {
    expanded.value = forms.value
      .map((form) => (form.item_groups.length > 0 ? form.name : null))
      .filter((val) => val !== null)

    expandGroupsForForm.value = forms.value
      .map((form) => (form.item_groups.length > 0 ? form.uid : null))
      .filter((val) => val !== null)
  }
}

function updateForm(form) {
  forms.value = forms.value.map((f) =>
    f.uid === form.uid ? { ...f, ...form } : f
  )
  emit('updateParentCollectionForm', props.parentCollection, form)
}

function openDefinition(item) {
  selectedForm.value = item
  showFormForm.value = true
}

function closeDefinition() {
  selectedForm.value = {}
  showFormForm.value = false
  fetchForms()
}

function openLinkForm(item) {
  selectedForm.value = item
  showLinkForm.value = true
}

function closeLinkForm() {
  showLinkForm.value = false
  selectedForm.value = {}
  fetchForms()
}

async function expandAll(item) {
  await expanded.value.push(item.name)
  expandGroupsForForm.value = [item.uid]
}

function openExportForm(item) {
  selectedForm.value = item
  showExportForm.value = true
}

function closeExportForm() {
  selectedForm.value = {}
  showExportForm.value = false
}

function editAttributes(item) {
  selectedForm.value = item
  showAttributesForm.value = true
}

function closeAttributesForm() {
  selectedForm.value = {}
  showAttributesForm.value = false
}

function previewODM(item) {
  router.push({
    name: 'CrfBuilder',
    params: {
      tab: 'odm-viewer',
      uid: item.uid,
      type: crfTypes.FORM,
    },
  })
}

function orderUp(item, index) {
  if (index === 0) {
    return
  }

  forms.value[index].order_number--
  forms.value[index - 1].order_number++
  forms.value.sort((a, b) => a.order_number - b.order_number)
  crfs.addFormsToCollection(forms.value, props.parentCollection.uid, true)
}

function orderDown(item, index) {
  if (index === forms.value.length - 1) {
    return
  }

  forms.value[index].order_number++
  forms.value[index + 1].order_number--
  forms.value.sort((a, b) => a.order_number - b.order_number)
  crfs.addFormsToCollection(forms.value, props.parentCollection.uid, true)
}
</script>
<style scoped>
#forms .v-table__wrapper > table > thead > tr {
  visibility: collapse;
}
</style>

<template>
  <v-card elevation="0" class="rounded-0">
    <v-card-title
      style="z-index: 3; position: relative"
      class="pt-0 mt-3 d-flex align-center"
    >
      <v-spacer />
      <slot name="topActions" />
      <v-btn
        v-if="copyFromStudy"
        data-cy="copy-from-study"
        class="ml-2"
        icon
        size="small"
        variant="outlined"
        color="nnBaseBlue"
        :disabled="
          !checkPermission($roles.STUDY_WRITE) ||
          studiesGeneralStore.selectedStudyVersion !== null
        "
        @click.stop="openCopyForm"
      >
        <v-icon>mdi-content-copy</v-icon>
        <v-tooltip activator="parent" location="top">
          {{ $t('NNTableTooltips.copy_from_study') }}
        </v-tooltip>
      </v-btn>
      <v-btn
        class="ml-2"
        icon
        size="small"
        variant="outlined"
        color="nnBaseBlue"
        :disabled="
          !checkPermission($roles.STUDY_WRITE) ||
          studiesGeneralStore.selectedStudyVersion !== null ||
          disableEdit
        "
        data-cy="edit-content"
        @click.stop="openForm"
      >
        <v-icon>mdi-pencil-outline</v-icon>
        <v-tooltip activator="parent" location="top">
          {{ $t('NNTableTooltips.edit_content') }}
        </v-tooltip>
      </v-btn>
      <v-btn
        class="ml-2"
        icon
        size="small"
        variant="outlined"
        color="nnBaseBlue"
        @click="openHistory"
      >
        <v-icon>mdi-history</v-icon>
        <v-tooltip activator="parent" location="top">
          {{ $t('NNTableTooltips.history') }}
        </v-tooltip>
      </v-btn>
    </v-card-title>
    <v-card-text>
      <v-data-table
        :headers="headers"
        :items="computedParams"
        item-value="name"
        :items-per-page="15"
        :items-per-page-options="itemsPerPageOptions"
      >
        <template #[`item.values`]="{ item }">
          <CTTermDisplay
            v-if="getParamDisplayType(item.key) === 'term'"
            :term="metadata[item.key]"
          />
          <template v-else-if="getParamDisplayType(item.key) === 'terms'">
            <template
              v-for="(term, index) in metadata[item.key]"
              :key="`${item.key}-${term.term_uid}`"
            >
              <CTTermDisplay :term="term" />
              <span v-if="index !== metadata[item.key].length - 1">, </span>
            </template>
          </template>
          <span v-else>
            {{ item.values }}
          </span>
        </template>
        <template #[`item.reason_for_missing`]="{ item }">
          <CTTermDisplay
            v-if="metadata[item.null_value_key]"
            :term="metadata[item.null_value_key]"
          />
        </template>
      </v-data-table>
      <slot
        name="form"
        :open-handler="showForm"
        :close-handler="closeForm"
        :data="metadata"
        :data-to-copy="dataToCopy"
        :form-key="formKey"
      />

      <v-dialog
        v-model="showCopyForm"
        persistent
        max-width="600px"
        @keydown.esc="closeCopyForm"
      >
        <CopyFromStudyForm
          :component="component"
          @close="closeCopyForm"
          @apply="openFormToCopy"
        />
      </v-dialog>

      <v-dialog
        v-model="showHistory"
        persistent
        :fullscreen="$globals.historyDialogFullscreen"
        @keydown.esc="closeHistory"
      >
        <HistoryTable
          :headers="historyHeaders"
          :items="historyItems"
          :items-total="historyItems.length"
          :title="historyTitle"
          :export-name="component"
          start-date-header="date"
          change-field="action"
          simple-styling
          @close="closeHistory"
        />
      </v-dialog>
    </v-card-text>
  </v-card>
</template>

<script>
import CopyFromStudyForm from '@/components/tools/CopyFromStudyForm.vue'
import HistoryTable from '@/components/tools/HistoryTable.vue'
import CTTermDisplay from '@/components/tools/CTTermDisplay.vue'
import study from '@/api/study'
import tablesConstants from '@/constants/tables'
import { useAccessGuard } from '@/composables/accessGuard'
import { useStudiesGeneralStore } from '@/stores/studies-general'

export default {
  components: {
    HistoryTable,
    CTTermDisplay,
    CopyFromStudyForm,
  },
  props: {
    params: {
      type: Array,
      default: () => [],
    },
    metadata: {
      type: Object,
      default: undefined,
    },
    firstColLabel: {
      type: String,
      default: '',
    },
    persistentDialog: {
      type: Boolean,
      default: false,
    },
    formMaxWidth: {
      type: String,
      required: false,
      default: '',
    },
    copyFromStudy: {
      type: Boolean,
      default: false,
    },
    component: {
      type: String,
      default: '',
    },
    withReasonForMissing: {
      type: Boolean,
      default: true,
    },
    disableEdit: {
      type: Boolean,
      default: false,
    },
  },
  setup() {
    const accessGuard = useAccessGuard()
    const studiesGeneralStore = useStudiesGeneralStore()

    return {
      studiesGeneralStore,
      ...accessGuard,
    }
  },
  data() {
    const headers = [
      { title: this.firstColLabel, key: 'name', width: '30%' },
      { title: this.$t('StudyMetadataSummary.selected_values'), key: 'values' },
    ]
    if (this.withReasonForMissing) {
      headers.push({
        title: this.$t('StudyMetadataSummary.reason_for_missing'),
        key: 'reason_for_missing',
      })
    }
    return {
      headers,
      historyHeaders: [
        { title: this.$t('HistoryTable.field'), key: 'field' },
        {
          title: this.$t('HistoryTable.value_before'),
          key: 'before_value.term_uid',
        },
        {
          title: this.$t('HistoryTable.value_after'),
          key: 'after_value.term_uid',
        },
        { title: this.$t('_global.user'), key: 'author_username' },
      ],
      historyItems: [],
      showHistory: false,
      showForm: false,
      formKey: 0,
      showCopyForm: false,
      dataToCopy: {},
    }
  },
  computed: {
    computedParams() {
      return this.buildTableParams(this.params)
    },
    historyTitle() {
      return `${this.component} ${this.$t('HistoryTable.fields')} ${this.$t('HistoryTable.history')} (${this.studiesGeneralStore.selectedStudy.uid})`
    },
    exportDataUrl() {
      return `studies/${this.studiesGeneralStore.selectedStudy.uid}`
    },
    itemsPerPageOptions() {
      return tablesConstants.ITEMS_PER_PAGE_OPIONS
    },
  },
  methods: {
    openForm() {
      this.formKey++
      this.showForm = true
    },
    closeForm() {
      this.dataToCopy = {}
      this.showForm = false
    },
    openFormToCopy(data) {
      this.dataToCopy = data
      this.formKey++
      this.showForm = true
      this.showCopyForm = false
    },
    openCopyForm() {
      this.showCopyForm = true
    },
    closeCopyForm() {
      this.showCopyForm = false
    },
    async openHistory() {
      this.historyItems = []
      const resp = await study.getStudyFieldsAuditTrail(
        this.studiesGeneralStore.selectedStudy.uid,
        this.component
      )
      for (const group of resp.data) {
        for (const groupItem of group.actions) {
          const row = {
            author_username: group.author_username,
            date: group.date,
            field: groupItem.field,
            action: groupItem.action,
            before_value: groupItem.before_value,
            after_value: groupItem.after_value,
          }
          this.historyItems.push(row)
        }
      }
      this.showHistory = true
    },
    closeHistory() {
      this.showHistory = false
    },
    getParamDisplayType(name) {
      const param = this.params.find((p) => p.name === name)
      return param.valuesDisplay
    },
    buildTableParams(fields) {
      const result = []
      fields.forEach((field) => {
        let nullValueName = null
        if (field.nullValueName === undefined) {
          nullValueName = field.name
          const suffixes = ['_code', '_codes']
          suffixes.forEach((suffix) => {
            if (nullValueName.endsWith(suffix)) {
              nullValueName = nullValueName.replace(suffix, '')
            }
          })
          nullValueName += '_null_value_code'
        } else {
          nullValueName = field.nullValueName
        }
        let values = this.metadata[field.name]
        if (
          field.name === 'sex_of_participants_code' &&
          values !== undefined &&
          values !== null
        ) {
          values = values.name
        }
        if (field.name === 'study_stop_rules' && values == null) {
          values = this.$t('StudyDefineForm.none')
        }
        result.push({
          key: field.name,
          name: field.label,
          values:
            values !== undefined &&
            values !== null &&
            field.valuesDisplay &&
            !['term', 'terms'].includes(field.valuesDisplay) &&
            field.name !== 'sex_of_participants_code'
              ? this[`${field.valuesDisplay}Display`](values)
              : values,
          null_value_key: nullValueName,
        })
      })
      return result
    },
    yesnoDisplay(value) {
      return value ? this.$t('_global.yes') : this.$t('_global.no')
    },
    durationDisplay(value) {
      return `${value.duration_value} ${value.duration_unit_code.name}`
    },
    dictionaryTermsDisplay(value) {
      const result = value.map((item) => item.name)
      return result.join(', ')
    },
  },
}
</script>

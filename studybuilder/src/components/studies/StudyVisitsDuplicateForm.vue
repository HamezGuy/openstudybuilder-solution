<template>
  <SimpleFormDialog
    ref="formRef"
    :title="$t('StudyVisitForm.duplicate_visit')"
    :open="open"
    max-width="400px"
    :help-items="helpItems"
    @close="close"
    @submit="submit"
  >
    <template #body>
      <v-form ref="observer">
        <v-text-field
          v-model="form.timing"
          :label="$t('StudyVisitForm.time_value')"
          :rules="[formRules.required]"
          type="number"
        />
        <v-autocomplete
          v-model="form.time_unit_uid"
          :label="$t('StudyVisitForm.time_unit_name')"
          data-cy="time-unit"
          :items="epochsStore.studyTimeUnits"
          item-title="name"
          item-value="uid"
          :rules="[formRules.required]"
          clearable
        />
      </v-form>
    </template>
  </SimpleFormDialog>
</template>

<script setup>
import { inject, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import SimpleFormDialog from '@/components/tools/SimpleFormDialog.vue'
import epochs from '@/api/studyEpochs'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import { useEpochsStore } from '@/stores/studies-epochs'
import visitConstants from '@/constants/visits'

const { t } = useI18n()
const notificationHub = inject('notificationHub')
const formRules = inject('formRules')
const props = defineProps({
  studyVisit: {
    type: Object,
    default: undefined,
  },
  open: Boolean,
})
const emit = defineEmits(['close'])
const studiesGeneralStore = useStudiesGeneralStore()
const epochsStore = useEpochsStore()

const form = ref({})
const formRef = ref()
const observer = ref()

const helpItems = [
  'StudyVisitDuplicate.time_value',
  'StudyVisitDuplicate.time_unit',
]

watch(
  () => props.studyVisit,
  (newVal) => {
    if (newVal) {
      form.value.time_unit_uid = newVal.time_unit_uid
    }
  },
  { immediate: true }
)

async function submit() {
  notificationHub.clearErrors()

  try {
    formRef.value.working = true
    // FIXME: Replaced structuredClone as a quickfix because it never returns for some reason...
    // const newVisit = structuredClone(props.studyVisit)
    const newVisit = JSON.parse(JSON.stringify(props.studyVisit))
    newVisit.time_value = form.value.timing
    newVisit.time_unit_uid = form.value.time_unit_uid
    if (newVisit.visit_class !== visitConstants.CLASS_MANUALLY_DEFINED_VISIT) {
      delete newVisit.visit_number
      delete newVisit.unique_visit_number
      delete newVisit.visit_short_name
      delete newVisit.visit_name
    }
    const resp = await epochs.getStudyVisitPreview(
      studiesGeneralStore.selectedStudy.uid,
      newVisit
    )
    const fields = [
      'study_day_label',
      'study_week_label',
      'study_day_number',
      'study_week_number',
      'duration_time',
    ]
    if (newVisit.visit_class === visitConstants.CLASS_MANUALLY_DEFINED_VISIT) {
      fields.push(
        'visit_number',
        'unique_visit_number',
        'visit_name',
        'visit_short_name'
      )
    }
    for (const field of fields) {
      newVisit[field] = resp.data[field]
    }
    await epochsStore.addStudyVisit({
      studyUid: studiesGeneralStore.selectedStudy.uid,
      input: newVisit,
    })
    notificationHub.add({
      msg: t('StudyVisitForm.visit_duplicated'),
    })
    close()
  } finally {
    formRef.value.working = false
  }
}

function close() {
  notificationHub.clearErrors()
  form.value = {}
  observer.value.reset()
  emit('close')
}
</script>

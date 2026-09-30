<template>
  <SimpleFormDialog
    ref="formRef"
    :title="title"
    :help-items="helpItems"
    :open="open"
    :transition="false"
    :no-saving="!canEdit || armLoading || Boolean(armLoadError)"
    @close="cancel"
    @submit="submit"
  >
    <template #body>
      <v-alert v-if="!canEdit && open" type="info" variant="tonal">
        {{ $t('StudyArmsForm.arm_read_only') }}
      </v-alert>
      <v-progress-linear
        v-if="armLoading"
        indeterminate
        :aria-label="$t('StudyArmsForm.loading_arm')"
      />
      <v-alert v-if="armLoadError" type="error" variant="tonal">
        {{ armLoadError }}
        <v-btn variant="text" @click="loadArm">
          {{ $t('StudyArmsForm.retry_arm') }}
        </v-btn>
      </v-alert>
      <v-form
        v-show="!armLoading && !armLoadError"
        ref="observer"
        :disabled="!canEdit || armLoading || Boolean(armLoadError)"
      >
        <template v-if="!originOnly">
          <v-row>
            <v-col cols="12">
              <v-autocomplete
                v-model="form.arm_type_uid"
                :label="$t('StudyArmsForm.arm_type')"
                :items="armTypes"
                item-title="sponsor_preferred_name"
                item-value="term_uid"
                data-cy="arm-type"
                clearable
              />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12">
              <v-text-field
                v-model="form.name"
                :label="$t('StudyArmsForm.arm_name')"
                data-cy="arm-name"
                :rules="[formRules.required, formRules.max(form.name, 200)]"
                clearable
              />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12">
              <v-text-field
                v-model="form.label"
                :label="$t('StudyArmsForm.arm_label')"
                data-cy="arm-label"
                :rules="[formRules.max(form.label, 40)]"
                clearable
              />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12">
              <v-text-field
                v-model="form.short_name"
                :label="$t('StudyArmsForm.arm_short_name')"
                data-cy="arm-short-name"
                :rules="[
                  formRules.required,
                  formRules.max(form.short_name, 20),
                ]"
                clearable
              />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12">
              <v-text-field
                v-model="form.randomization_group"
                :label="$t('StudyArmsForm.randomisation_group')"
                data-cy="arm-randomisation-group"
                clearable
                @blur="enableArmCode"
              />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12">
              <v-text-field
                v-model="form.code"
                :label="$t('StudyArmsForm.arm_code')"
                data-cy="arm-code"
                :rules="[formRules.max(form.code, 20)]"
                clearable
                :disabled="!armCodeEnable && !isEdit()"
              />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12">
              <v-text-field
                v-model="form.number_of_subjects"
                :label="$t('StudyArmsForm.planned_number')"
                data-cy="arm-planned-number-of-subjects"
                :rules="[formRules.min_value(form.number_of_subjects, 1)]"
                type="number"
                clearable
              />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12">
              <v-text-field
                v-model="form.description"
                :label="$t('StudyArmsForm.description')"
                data-cy="arm-description"
                clearable
              />
            </v-col>
          </v-row>
        </template>
        <v-row>
          <v-col cols="12">
            <v-autocomplete
              v-model="form.data_origin_type_uid"
              :label="$t('StudyArmsForm.data_origin_type')"
              :items="armDataOrigins"
              item-title="sponsor_preferred_name"
              item-value="term_uid"
              :loading="originLoading"
              :disabled="originLoading || Boolean(originLoadError)"
              :rules="
                hasText(form.data_origin_description)
                  ? [formRules.required]
                  : []
              "
              :hint="$t('StudyArmsForm.data_origin_hint')"
              persistent-hint
              data-cy="arm-data-origin"
              clearable
            />
            <v-alert
              v-if="originLoadError"
              type="error"
              variant="tonal"
              class="mt-2"
            >
              {{ originLoadError }}
              <v-btn
                variant="text"
                :loading="originLoading"
                @click="loadArmDataOrigins"
              >
                {{ $t('StudyArmsForm.retry_origin_terms') }}
              </v-btn>
            </v-alert>
            <v-alert
              v-else-if="!originLoading && armDataOrigins.length === 0"
              type="info"
              variant="tonal"
              class="mt-2"
            >
              {{ $t('StudyArmsForm.no_origin_terms') }}
            </v-alert>
            <p v-if="recordedOriginOutsideList" class="mt-2">
              {{
                $t('StudyArmsForm.recorded_origin', {
                  uid: form.data_origin_type_uid,
                })
              }}
            </p>
            <v-alert
              v-if="originPairError"
              type="error"
              variant="tonal"
              class="mt-2"
            >
              {{ originPairError }}
            </v-alert>
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="12">
            <v-textarea
              v-model="form.data_origin_description"
              :label="$t('StudyArmsForm.data_origin_description')"
              :rules="
                hasText(form.data_origin_type_uid) ? [formRules.required] : []
              "
              data-cy="arm-data-origin-description"
              auto-grow
              rows="2"
              clearable
            />
            <v-btn
              v-if="
                typeof form.data_origin_type_uid === 'string' ||
                typeof form.data_origin_description === 'string'
              "
              variant="text"
              data-cy="clear-arm-data-origin"
              @click="clearOrigin"
            >
              {{ $t('StudyArmsForm.clear_origin') }}
            </v-btn>
          </v-col>
        </v-row>
        <v-text-field
          v-if="isEdit() && !originOnly"
          v-model="branches"
          :label="$t('StudyArmsForm.connected_branches')"
          data-cy="arm-connected-branches"
          clearable
          readonly
        />
      </v-form>
    </template>
  </SimpleFormDialog>
</template>

<script setup>
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import arms from '@/api/arms'
import codelists from '@/api/controlledTerminology/terms'
import SimpleFormDialog from '@/components/tools/SimpleFormDialog.vue'
import _isEqual from 'lodash/isEqual'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import { useFormStore } from '@/stores/form'
import { useI18n } from 'vue-i18n'
import { useAccessGuard } from '@/composables/accessGuard'

const formRules = inject('formRules')
const notificationHub = inject('notificationHub')
const roles = inject('roles')

const props = defineProps({
  editedArm: {
    type: Object,
    default: () => ({}),
  },
  open: Boolean,
  originOnly: Boolean,
})
const emit = defineEmits(['close'])

const { t } = useI18n()
const studiesGeneralStore = useStudiesGeneralStore()
const formStore = useFormStore()
const accessGuard = useAccessGuard()

const selectedStudy = computed(() => studiesGeneralStore.selectedStudy)
const canEdit = computed(
  () =>
    studiesGeneralStore.selectedStudyVersion === null &&
    accessGuard.checkPermission(roles.STUDY_WRITE)
)
const title = computed(() => {
  if (props.originOnly) return t('StudyArmsForm.edit_data_origin')
  return Object.keys(props.editedArm).length !== 0
    ? t('StudyArmsForm.edit_arm')
    : t('StudyArmsForm.add_arm')
})

const form = ref({})
const armLoading = ref(false)
const armLoadError = ref(null)
let armLoadSequence = 0
let active = true
let loadedStudyUid = null
let loadedArmUid = null
const armTypes = ref([])
const armDataOrigins = ref([])
const originLoading = ref(true)
const originLoadError = ref(null)
let originLoadSequence = 0
const originPairError = ref(null)
const recordedOriginOutsideList = computed(
  () =>
    !originLoading.value &&
    !originLoadError.value &&
    hasText(form.value.data_origin_type_uid) &&
    !armDataOrigins.value.some(
      (term) => term.term_uid === form.value.data_origin_type_uid
    )
)
const armCodeEnable = ref(false)
const branches = ref([])
const formRef = ref()
const observer = ref()
let storedForm = ''

const helpItems = computed(() =>
  props.originOnly
    ? [
        'StudyArmsForm.data_origin_type',
        'StudyArmsForm.data_origin_description',
      ]
    : [
        'StudyArmsForm.arm_type',
        'StudyArmsForm.arm_name',
        'StudyArmsForm.arm_short_name',
        'StudyArmsForm.arm_label',
        'StudyArmsForm.randomisation_group',
        'StudyArmsForm.arm_code',
        'StudyArmsForm.planned_number',
        'StudyArmsForm.description',
        'StudyArmsForm.data_origin_type',
        'StudyArmsForm.data_origin_description',
      ]
)

watch(
  () => [props.open, props.editedArm.arm_uid, selectedStudy.value?.uid],
  ([isOpen]) => {
    if (isOpen) {
      loadArm()
      loadArmDataOrigins()
    } else {
      armLoadSequence++
    }
  },
  { immediate: true }
)

watch(
  () => [form.value.data_origin_type_uid, form.value.data_origin_description],
  () => {
    originPairError.value = null
  }
)

onMounted(() => {
  codelists.getTermsByCodelist('armTypes').then((resp) => {
    armTypes.value = resp.data.items
  })
})

onBeforeUnmount(() => {
  active = false
  armLoadSequence++
  originLoadSequence++
})

function stopWorking() {
  if (formRef.value) formRef.value.working = false
}

function hasText(value) {
  return typeof value === 'string' && value.trim().length > 0
}
async function loadArm() {
  const sequence = ++armLoadSequence
  const studyUid = selectedStudy.value?.uid
  const armUid = props.editedArm.arm_uid
  armLoadError.value = null
  armLoading.value = true
  loadedStudyUid = null
  loadedArmUid = null
  form.value = {}
  branches.value = []
  storedForm = ''
  try {
    if (
      !props.open ||
      !hasText(studyUid) ||
      (props.originOnly && !hasText(armUid))
    ) {
      throw new Error('Arm edit identity is incomplete')
    }
    if (isEdit()) {
      const response = await arms.getStudyArm(studyUid, armUid)
      if (sequence !== armLoadSequence || !props.open) return
      const data = response.data
      if (
        data?.arm_uid !== armUid ||
        data.study_uid !== studyUid ||
        !Object.hasOwn(data, 'data_origin_type_uid') ||
        !Object.hasOwn(data, 'data_origin_description') ||
        !(
          (data.data_origin_type_uid === null &&
            data.data_origin_description === null) ||
          (hasText(data.data_origin_type_uid) &&
            hasText(data.data_origin_description))
        )
      )
        throw new Error(
          'Arm response identity or data origin contract is incomplete'
        )
      form.value = JSON.parse(JSON.stringify(data))
      if (form.value.arm_connected_branch_arms) {
        branches.value = form.value.arm_connected_branch_arms.map(
          (el) => el.name
        )
        delete form.value.arm_connected_branch_arms
      }
      form.value.arm_type_uid = form.value.arm_type?.term_uid ?? null
      loadedArmUid = armUid
    } else {
      form.value = { data_origin_type_uid: null, data_origin_description: null }
    }
    loadedStudyUid = studyUid
    storedForm = JSON.stringify(form.value)
    formStore.save(form.value)
  } catch {
    if (sequence === armLoadSequence && props.open) {
      armLoadError.value = t('StudyArmsForm.arm_load_failed')
    }
  } finally {
    if (sequence === armLoadSequence) armLoading.value = false
  }
}
function clearOrigin() {
  form.value.data_origin_type_uid = null
  form.value.data_origin_description = null
}
async function loadArmDataOrigins() {
  const sequence = ++originLoadSequence
  originLoading.value = true
  originLoadError.value = null
  armDataOrigins.value = []
  try {
    const response = await codelists.getTermsByCodelist('armDataOrigins', {
      all: true,
      total_count: true,
    })
    if (sequence !== originLoadSequence) return
    if (
      !Array.isArray(response.data.items) ||
      response.data.total !== response.data.items.length
    ) {
      throw new Error('Incomplete arm origin terminology response')
    }
    const approved = response.data.items.filter(
      (term) =>
        term.name_status === 'Final' && term.attributes_status === 'Final'
    )
    if (
      approved.some(
        (term) =>
          !hasText(term.term_uid) || !hasText(term.sponsor_preferred_name)
      ) ||
      new Set(approved.map((term) => term.term_uid)).size !== approved.length
    ) {
      throw new Error('Incomplete or ambiguous arm origin terminology')
    }
    armDataOrigins.value = approved
  } catch {
    if (sequence === originLoadSequence)
      originLoadError.value = t('StudyArmsForm.origin_terms_failed')
  } finally {
    if (sequence === originLoadSequence) originLoading.value = false
  }
}
function enableArmCode() {
  if (!armCodeEnable.value) {
    form.value.code = form.value.randomization_group
    armCodeEnable.value = true
  }
}
function isEdit() {
  return Object.keys(props.editedArm).length !== 0
}
async function submit() {
  const sequence = armLoadSequence
  notificationHub.clearErrors()
  if (
    !active ||
    !props.open ||
    !canEdit.value ||
    armLoading.value ||
    armLoadError.value ||
    loadedStudyUid !== selectedStudy.value?.uid ||
    (isEdit() && loadedArmUid !== props.editedArm.arm_uid) ||
    (props.originOnly && !isEdit())
  ) {
    stopWorking()
    return
  }
  if (
    !(
      (form.value.data_origin_type_uid === null &&
        form.value.data_origin_description === null) ||
      (hasText(form.value.data_origin_type_uid) &&
        hasText(form.value.data_origin_description))
    )
  ) {
    originPairError.value = t('StudyArmsForm.origin_pair_required')
    stopWorking()
    return
  }
  const { valid } = await observer.value.validate()
  if (!valid) {
    stopWorking()
    return
  }
  if (
    !active ||
    !props.open ||
    sequence !== armLoadSequence ||
    !canEdit.value ||
    armLoading.value ||
    armLoadError.value ||
    loadedStudyUid !== selectedStudy.value?.uid ||
    (isEdit() && loadedArmUid !== props.editedArm.arm_uid)
  ) {
    stopWorking()
    return
  }

  if (Object.keys(props.editedArm).length !== 0) {
    edit()
  } else {
    create()
  }
}
function create() {
  const sequence = armLoadSequence
  arms.create(selectedStudy.value.uid, form.value).then(
    () => {
      notificationHub.add({
        msg: t('StudyArmsForm.arm_created'),
      })
      if (sequence === armLoadSequence && props.open) close()
    },
    () => {
      if (sequence === armLoadSequence) stopWorking()
    }
  )
}
function edit() {
  const sequence = armLoadSequence
  const payload = props.originOnly
    ? {
        data_origin_type_uid: form.value.data_origin_type_uid,
        data_origin_description: form.value.data_origin_description,
      }
    : form.value
  arms.edit(selectedStudy.value.uid, payload, props.editedArm.arm_uid).then(
    () => {
      notificationHub.add({
        msg: t('StudyArmsForm.arm_updated'),
      })
      if (sequence === armLoadSequence && props.open) close()
    },
    () => {
      if (sequence === armLoadSequence) stopWorking()
    }
  )
}
function close() {
  armLoadSequence++
  notificationHub.clearErrors()
  form.value = {}
  armCodeEnable.value = false
  observer.value?.reset()
  emit('close')
  formStore.reset()
}
async function cancel() {
  if (storedForm === '' || _isEqual(storedForm, JSON.stringify(form.value))) {
    close()
  } else {
    const options = {
      type: 'warning',
      cancelLabel: t('_global.cancel'),
      agreeLabel: t('_global.continue'),
    }
    if (await formRef.value.confirm(t('_global.cancel_changes'), options)) {
      close()
    }
  }
}
</script>

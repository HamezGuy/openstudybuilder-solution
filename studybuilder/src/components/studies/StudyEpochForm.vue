<template>
  <SimpleFormDialog
    ref="formRef"
    :title="title"
    :help-items="helpItems"
    :help-text="$t('_help.StudyDefineForm.general')"
    :open="open"
    @close="cancel"
    @submit="submit"
  >
    <template #body>
      <v-alert
        v-if="studyEpoch && studyEpoch.study_visit_count > 0"
        type="warning"
      >
        {{
          $t('StudyEpochForm.epoch_linked_to_visits_warning', {
            epoch: studyEpoch.epoch_name,
          })
        }}
      </v-alert>
      <v-form ref="observer">
        <v-row>
          <v-col cols="6">
            <v-autocomplete
              v-model="form.epoch_type"
              :label="$t('StudyEpochForm.epoch_type')"
              :items="uniqueTypeGroups"
              item-title="type_name"
              item-value="type"
              :rules="[formRules.required]"
              autocomplete="off"
              clearable
              data-cy="epoch-type"
              :disabled="studyEpoch ? true : false"
              class="required"
              @update:model-value="setEpochGroups()"
              @click:clear="setEpochGroups()"
            />
          </v-col>
          <v-col cols="6">
            <v-autocomplete
              v-model="form.epoch_subtype"
              :label="$t('StudyEpochForm.epoch_subtype')"
              :items="subtypeGroups"
              item-title="subtype_name"
              item-value="subtype"
              :rules="[formRules.required]"
              autocomplete="off"
              clearable
              data-cy="epoch-subtype"
              :disabled="studyEpoch ? true : false"
              class="required"
              @update:model-value="setEpochGroups()"
              @click:clear="setEpochGroups()"
            />
          </v-col>
        </v-row>
        <v-row>
          <v-col>
            <v-text-field
              :key="typeTrigger"
              v-model="epochDisplay"
              :rules="[formRules.required]"
              data-cy="select-epoch"
              :label="$t('StudyEpochForm.name')"
              :loading="epochNameLoading"
              disabled
            />
          </v-col>
        </v-row>
        <v-row>
          <v-col>
            <v-textarea
              id="startRule"
              v-model="form.start_rule"
              data-cy="epoch-start-rule"
              :label="$t('StudyEpochForm.start_rule')"
              rows="1"
              auto-grow
            />
          </v-col>
        </v-row>
        <v-row>
          <v-col>
            <v-textarea
              id="endRule"
              v-model="form.end_rule"
              data-cy="epoch-end-rule"
              :label="$t('StudyEpochForm.stop_rule')"
              rows="1"
              auto-grow
            />
          </v-col>
        </v-row>
        <v-row>
          <v-col>
            <v-textarea
              id="description"
              v-model="form.description"
              data-cy="description"
              :label="$t('StudyEpochForm.description')"
              rows="1"
              auto-grow
            />
          </v-col>
        </v-row>
        <div v-if="studyEpoch">
          <label class="v-label required">{{
            $t('_global.change_description')
          }}</label>
          <v-textarea
            v-model="form.change_description"
            :rules="[formRules.required]"
            clearable
            rows="1"
            auto-grow
          />
        </div>
        <div class="mt-4">
          <label class="v-label">{{ $t('StudyEpochForm.color') }}</label>
          <v-color-picker
            v-model="colorHash"
            data-cy="epoch-color-picker"
            clearable
            show-swatches
            hide-canvas
            hide-sliders
            swatches-max-height="300px"
          />
        </div>
      </v-form>
    </template>
  </SimpleFormDialog>
</template>

<script setup>
import _isEmpty from 'lodash/isEmpty'
import _isEqual from 'lodash/isEqual'
import units from '@/api/units'
import studyEpochsApi from '@/api/studyEpochs'
import SimpleFormDialog from '@/components/tools/SimpleFormDialog.vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import { useEpochsStore } from '@/stores/studies-epochs'
import { useI18n } from 'vue-i18n'
import { computed, inject, onMounted, ref, watch } from 'vue'

const notificationHub = inject('notificationHub')
const formRules = inject('formRules')

const props = defineProps({
  studyEpoch: {
    type: Object,
    default: undefined,
  },
  open: Boolean,
})

const emit = defineEmits(['close'])

const { t } = useI18n()
const studiesGeneralStore = useStudiesGeneralStore()
const epochsStore = useEpochsStore()

const colorHash = ref(null)
const form = ref({})

const helpItems = [
  'StudyEpochForm.name',
  'StudyEpochForm.epoch_type',
  'StudyEpochForm.epoch_subtype',
  'StudyEpochForm.description',
  'StudyEpochForm.start_rule',
  'StudyEpochForm.stop_rule',
  'StudyEpochForm.epoch_time_unit',
  'StudyEpochForm.expected_epoch_duration',
  'StudyEpochForm.color',
]

const timeUnits = ref([])
const typeTrigger = ref(0)
const typeGroups = ref([])
const subtypeGroups = ref([])
const epochDisplay = ref('')
const epochNameLoading = ref(false)
const observer = ref()
const formRef = ref()

const selectedStudy = computed(() => studiesGeneralStore.selectedStudy)
const groups = computed(() => epochsStore.allowedConfigs)
const uniqueTypeGroups = computed(() => {
  const result = []
  for (let group of typeGroups.value) {
    if (!result.find((item) => item.type === group.type)) {
      result.push(group)
    }
  }
  return result
})
const title = computed(() => {
  return props.studyEpoch
    ? t('StudyEpochForm.edit_title')
    : t('StudyEpochForm.add_title')
})

watch(
  () => props.studyEpoch,
  (value) => {
    if (value) {
      studyEpochsApi
        .getStudyEpoch(selectedStudy.value.uid, value.uid)
        .then((resp) => {
          loadFromStudyEpoch(resp.data)
        })
    }
  }
)
watch(groups, () => {
  typeGroups.value = groups.value
  subtypeGroups.value = groups.value
})

onMounted(() => {
  epochsStore.fetchAllowedConfigs()
  units.getByDimension('TIME').then((resp) => {
    timeUnits.value = resp.data.items
  })
  if (props.studyEpoch) {
    loadFromStudyEpoch(props.studyEpoch)
    setEpochGroups()
  }
})

function close() {
  emit('close')
  notificationHub.clearErrors()
  form.value = {}
  colorHash.value = null
  observer.value.reset()
  typeGroups.value = groups.value
  subtypeGroups.value = groups.value
}

async function cancel() {
  if (
    (!props.studyEpoch && !_isEmpty(form.value)) ||
    (props.studyEpoch && !_isEqual(form.value, props.studyEpoch))
  ) {
    const options = {
      type: 'warning',
      cancelLabel: t('_global.cancel'),
      agreeLabel: t('_global.continue'),
    }
    if (await formRef.value.confirm(t('_global.cancel_changes'), options)) {
      close()
    }
  } else {
    close()
  }
}

async function setEpochGroups() {
  formRef.value.working = true

  try {
    let type = ''
    let subtype = ''

    if (!form.value.epoch_subtype && !form.value.epoch_type) {
      form.value.epoch = ''
      subtypeGroups.value = groups.value
      typeGroups.value = groups.value
      epochDisplay.value = ''
      return
    }

    if (!form.value.epoch_subtype) {
      form.value.epoch = ''
      type = form.value.epoch_type
      subtypeGroups.value = groups.value
      typeGroups.value = groups.value
      subtypeGroups.value = groups.value.filter(function (value) {
        return value.type === type
      })
      epochDisplay.value = ''
      return
    }

    if (!form.value.epoch_type) {
      form.value.epoch = ''
      subtype = form.value.epoch_subtype
      typeGroups.value = groups.value
      subtypeGroups.value = groups.value
      typeGroups.value = groups.value.filter(function (value) {
        return value.subtype === subtype
      })
      epochDisplay.value = ''
      return
    }

    subtype = form.value.epoch_subtype
    type = form.value.epoch_type
    typeGroups.value = groups.value.filter(function (value) {
      return value.subtype === subtype
    })
    subtypeGroups.value = groups.value.filter(function (value) {
      return value.type === type
    })

    epochNameLoading.value = true
    const data = {
      study_uid: selectedStudy.value.uid,
      epoch_subtype: subtype,
    }
    const resp = await studyEpochsApi.getPreviewEpoch(
      selectedStudy.value.uid,
      data
    )
    form.value.epoch = resp.data.epoch
    epochDisplay.value = resp.data.epoch_name
  } finally {
    epochNameLoading.value = false
    formRef.value.working = false
  }
}

async function submit() {
  notificationHub.clearErrors()

  try {
    if (!props.studyEpoch) {
      await addObject()
    } else {
      await updateObject()
    }
    close()
  } finally {
    formRef.value.working = false
  }
}

function addObject() {
  const data = JSON.parse(JSON.stringify(form.value))
  if (colorHash.value) {
    data.color_hash = colorHash.value
  } else {
    data.color_hash = '#BDBDBD'
  }
  data.study_uid = selectedStudy.value.uid
  return epochsStore
    .addStudyEpoch({
      studyUid: selectedStudy.value.uid,
      input: data,
    })
    .then(() => {
      epochsStore.fetchStudyEpochs({ studyUid: selectedStudy.value.uid })
      notificationHub.add({
        msg: t('StudyEpochForm.add_success'),
      })
    })
}

function updateObject() {
  const data = JSON.parse(JSON.stringify(form.value))
  if (colorHash.value) {
    data.color_hash =
      colorHash.value.hexa !== undefined
        ? colorHash.value.hexa
        : colorHash.value
  } else {
    data.color_hash = '#BDBDBD'
  }
  return epochsStore
    .updateStudyEpoch({
      studyUid: selectedStudy.value.uid,
      studyEpochUid: props.studyEpoch.uid,
      input: data,
    })
    .then(() => {
      epochsStore.fetchStudyEpochs({ studyUid: selectedStudy.value.uid })
      notificationHub.add({
        msg: t('StudyEpochForm.update_success'),
      })
    })
}

function loadFromStudyEpoch(studyEpoch) {
  typeGroups.value = [
    {
      type: studyEpoch.epoch_type_ctterm.term_uid,
      type_name: studyEpoch.epoch_type_ctterm.sponsor_preferred_name,
    },
  ]
  subtypeGroups.value = [
    {
      subtype: studyEpoch.epoch_subtype_ctterm.term_uid,
      subtype_name: studyEpoch.epoch_subtype_ctterm.sponsor_preferred_name,
    },
  ]

  form.value = { ...studyEpoch }
  form.value.epoch_type = uniqueTypeGroups.value.find(
    (group) => group.type_name === form.value.epoch_type_name
  )
  form.value.epoch_subtype = subtypeGroups.value.find(
    (group) => group.subtype_name === form.value.epoch_subtype_name
  ).subtype
  epochDisplay.value = studyEpoch.epoch_name
  if (studyEpoch.color_hash) {
    colorHash.value = studyEpoch.color_hash
  }
}
</script>

<style>
.v-color-picker__controls {
  display: none;
}
</style>

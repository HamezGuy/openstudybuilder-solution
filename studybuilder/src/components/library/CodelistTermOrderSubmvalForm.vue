<template>
  <v-card data-cy="form-body" color="dfltBackground">
    <v-card-title class="d-flex align-center">
      <span class="dialog-title">{{
        $t('CodelistTermOrderSubmvalForm.order_and_submval_edit')
      }}</span>
      <HelpButtonWithPanels :title="$t('_global.help')" :items="helpItems" />
    </v-card-title>
    <v-card-text class="mt-4">
      <div class="bg-white pa-4">
        <v-row>
          <v-col cols="2">
            {{ $t('CodelistTermOrderSubmvalForm.term') }}
          </v-col>
          <v-col>
            {{ escapeHTMLHandler(termName) }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2">
            {{ $t('CodelistTermOrderSubmvalForm.codelist') }}
          </v-col>
          <v-col>
            {{ escapeHTMLHandler(codelistName) }}
          </v-col>
        </v-row>

        <v-form ref="observer">
          <v-combobox
            v-model="form.submissionValue"
            :items="submissionValues"
            data-cy="term-submission-value"
            :label="$t('CodelistTermDetail.submission_value')"
            :rules="[formRules.required]"
            clearable
          />
          <v-text-field
            v-model="form.order"
            data-cy="term-order"
            :label="$t('CodelistTermCreationForm.order')"
            :rules="[formRules.required]"
            clearable
          />
        </v-form>
      </div>
    </v-card-text>
    <v-card-actions>
      <v-spacer />
      <v-btn class="secondary-btn" color="white" @click="close">
        {{ $t('_global.cancel') }}
      </v-btn>
      <v-btn
        data-cy="save-button"
        color="secondary"
        :loading="working"
        @click="submit"
      >
        {{ $t('_global.save') }}
      </v-btn>
    </v-card-actions>
  </v-card>
</template>

<script setup>
import { inject, ref, watch } from 'vue'
import { escapeHTML } from '@/utils/sanitize'
import { useI18n } from 'vue-i18n'
import controlledTerminology from '@/api/controlledTerminology'
import HelpButtonWithPanels from '@/components/tools/HelpButtonWithPanels.vue'

const formRules = inject('formRules')
const notificationHub = inject('notificationHub')

const props = defineProps({
  termUid: {
    type: String,
    default: null,
  },
  codelistUid: {
    type: String,
    default: null,
  },
  submissionValue: {
    type: String,
    default: null,
  },
  order: {
    type: Number,
    default: null,
  },
  codelistName: {
    type: String,
    default: null,
  },
  termName: {
    type: String,
    default: null,
  },
  submissionValues: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['close', 'update:modelValue'])
const { t } = useI18n()

const form = ref({})
const observer = ref()
const working = ref(false)

const helpItems = [
  'CodelistTermCreationForm.submission_value',
  'CodelistTermCreationForm.order',
]

watch(
  () => props.termUid,
  (val) => {
    if (val) {
      form.value = {
        submissionValue: props.submissionValue,
        order: props.order,
      }
    }
  },
  { immediate: true }
)

function close() {
  emit('close')
  notificationHub.clearErrors()
}

async function submit() {
  const { valid } = await observer.value.validate()
  if (!valid) return

  notificationHub.clearErrors()

  working.value = true
  try {
    const orderData = {
      codelist_uid: props.codelistUid,
      order: form.value.order,
      submission_value: form.value.submissionValue,
    }
    await controlledTerminology.updateCodelistTermOrderSubmval(
      props.termUid,
      orderData
    )
    notificationHub.add({
      msg: t('CodelistTermOrderSubmvalForm.update_success'),
    })
    close()
  } finally {
    working.value = false
  }
}

function escapeHTMLHandler(html) {
  return escapeHTML(html)
}
</script>

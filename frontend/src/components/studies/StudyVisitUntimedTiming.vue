<template>
  <div data-cy="untimed-visit-timing">
    <v-alert type="info" variant="tonal" class="mb-4">
      {{ $t('StudyVisitForm.untimed_help') }}
    </v-alert>
    <v-select
      :model-value="modelValue?.kind"
      :items="kinds"
      :label="$t('StudyVisitForm.untimed_kind')"
      :rules="[formRules.required]"
      class="required"
      data-cy="untimed-kind"
      @update:model-value="selectKind"
    />
    <v-select
      v-if="modelValue?.kind === 'manual_date'"
      :model-value="modelValue.repeating"
      :items="repeatChoices"
      :label="$t('StudyVisitForm.untimed_repeatable')"
      :rules="[
        (value) =>
          typeof value === 'boolean' || $t('StudyVisitForm.untimed_required'),
      ]"
      data-cy="untimed-repeatable"
      @update:model-value="update('repeating', $event)"
    />
    <v-autocomplete
      v-if="modelValue?.kind && modelValue.kind !== 'manual_date'"
      :model-value="modelValue.anchor_visit_uid"
      :items="availableAnchors"
      item-title="visit_name"
      item-value="uid"
      :label="$t('StudyVisitForm.untimed_anchor')"
      :rules="[formRules.required]"
      clearable
      class="required"
      data-cy="untimed-anchor"
      @update:model-value="update('anchor_visit_uid', $event)"
    />
    <v-text-field
      v-if="modelValue?.kind === 'event_relative'"
      :model-value="modelValue.nominal_offset_days"
      type="number"
      :label="$t('StudyVisitForm.untimed_nominal_days')"
      :rules="[formRules.required, integer]"
      data-cy="untimed-nominal-days"
      @update:model-value="update('nominal_offset_days', $event)"
    />
    <v-row v-if="modelValue?.kind === 'calendar_repeat'">
      <v-col v-for="field in calendarFields" :key="field" cols="12" md="4">
        <v-text-field
          :model-value="modelValue[field]"
          type="number"
          :label="$t(`StudyVisitForm.untimed_${field}`)"
          :rules="[
            formRules.required,
            positiveInteger,
            ...(field === 'last_occurrence' ? [ordered] : []),
          ]"
          :data-cy="`untimed-${field}`"
          @update:model-value="update(field, $event)"
        />
      </v-col>
    </v-row>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  modelValue: { type: Object, default: null },
  visits: { type: Array, default: () => [] },
  currentVisitUid: { type: String, default: null },
})
const emit = defineEmits(['update:modelValue'])
const formRules = inject('formRules')
const { t } = useI18n()
const calendarFields = [
  'interval_months',
  'first_occurrence',
  'last_occurrence',
]
const kinds = computed(() =>
  ['manual_date', 'event_relative', 'calendar_repeat'].map((value) => ({
    value,
    title: t(`StudyVisitForm.untimed_kind_${value}`),
  }))
)
const repeatChoices = computed(() => [
  { value: false, title: t('StudyVisitForm.untimed_once') },
  { value: true, title: t('StudyVisitForm.untimed_repeat') },
])
const availableAnchors = computed(() =>
  props.visits.filter((visit) => visit.uid !== props.currentVisitUid)
)
function selectKind(kind) {
  emit('update:modelValue', { kind })
}
function update(field, value) {
  emit('update:modelValue', { ...props.modelValue, [field]: value })
}
function integer(value) {
  return (
    Number.isSafeInteger(Number(value)) || t('StudyVisitForm.untimed_integer')
  )
}
function positiveInteger(value) {
  return (
    (Number.isSafeInteger(Number(value)) && Number(value) > 0) ||
    t('StudyVisitForm.untimed_positive_integer')
  )
}
function ordered(value) {
  return (
    Number(value) >= Number(props.modelValue.first_occurrence) ||
    t('StudyVisitForm.untimed_ordered')
  )
}
</script>

<template>
  <td>
    <div v-for="cohort in visible" :key="cohort.cohort_uid" class="mb-1">
      <div class="text-body-large line-height-125">{{ cohort.name }}</div>
      <div v-if="cohort.number_of_subjects != null" class="text-body-medium text-gray">
        {{ cohort.number_of_subjects }} {{ $t('StudyStructureOverview.subjects') }}
      </div>
    </div>
  </td>
</template>

<script setup>
import { computed } from 'vue'
import { cohortsForStructureRow } from '@/utils/studyStructureCohorts'

const props = defineProps({
  cohorts: { type: Array, required: true },
  armUid: { type: String, required: true },
  branchUid: { type: String, default: null },
})
const visible = computed(() => cohortsForStructureRow(props.cohorts, props.armUid, props.branchUid))
</script>

<template>
  <div class="pa-4 bg-white" style="overflow-x: auto">
    <v-skeleton-loader
      v-if="designLoading"
      class="mx-auto"
      max-width="800px"
      type="table-heading, table-thead, table-tbody"
    />
    <v-alert v-else-if="designError" type="error" variant="tonal">
      {{ $t('StudyStructureOverview.design_load_failed') }}
      <v-btn variant="text" @click="loadDesign">{{ $t('_global.retry') }}</v-btn>
    </v-alert>
    <template v-else>
      <v-sheet
        :elevation="4"
        :height="200"
        class="pa-5 text-nn-blue"
        border
        rounded="lg"
      >
        <v-row>
          <v-col>
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.arms_number') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{ arms.length }}
            </div>
          </v-col>
          <v-col>
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.number_branches') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{ branches.length }}
            </div>
          </v-col>
          <v-col>
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.number_cohorts') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{ cohorts.length }}
            </div>
          </v-col>
          <v-col>
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.planned_subjects') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{
                populationLoading
                  ? 'Loading…'
                  : populationError
                    ? 'Unavailable'
                    : (plannedNumberOfSubjects ?? 'Not configured')
              }}
            </div>
          </v-col>
          <v-col>
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.number_elements') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{ elementsLoading ? 'Loading…' : (elementCount ?? 'Unavailable') }}
            </div>
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="4">
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.study_design_class') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{ designClass }}
            </div>
          </v-col>
          <v-col v-if="sourceVariable?.source_variable" cols="2">
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.source_var') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{ sourceVariable.source_variable }}
            </div>
          </v-col>
          <v-col v-if="sourceVariable?.source_variable">
            <div class="text-body-large line-height-125">
              {{ $t('StudyStructureOverview.source_var_desc') }}
            </div>
            <div class="text-headline-small font-weight-bold mt-1">
              {{ sourceVariable.source_variable_description }}
            </div>
          </v-col>
        </v-row>
      </v-sheet>
      <table
        class="mt-4"
        :aria-label="$t('StudyStructureOverview.table_caption')"
      >
        <thead>
          <tr>
            <th colspan="3" scope="col" style="color: white">
              {{ $t('StudyStructureOverview.study_structure') }}
            </th>
            <th
              v-if="visibleStudyEpochs.length > 0"
              :colspan="visibleStudyEpochs.length"
              scope="col"
              style="color: white"
            >
              {{ $t('StudyStructureOverview.epochs') }}
            </th>
          </tr>
          <tr>
            <th scope="col">
              {{ $t('StudyStructureOverview.arms') }}
            </th>
            <th scope="col">
              {{ $t('StudyStructureOverview.branch_arms') }}
            </th>
            <th scope="col">
              {{ $t('StudyStructureOverview.cohorts') }}
            </th>
            <td v-for="studyEpoch in visibleStudyEpochs" :key="studyEpoch.uid">
              {{ studyEpoch.epoch_name }}
            </td>
          </tr>
        </thead>
        <tbody>
          <tr v-if="arms.length === 0">
            <td :colspan="3 + visibleStudyEpochs.length" class="py-3">
              {{ $t('StudyStructureOverview.no_arms') }}
              <router-link
                class="ms-3"
                :to="{ name: 'StudyStructure', params: { tab: 'visits' } }"
              >
                {{ $t('Sidebar.study.study_visits') }}
              </router-link>
              <router-link
                class="ms-3"
                :to="{ name: 'StudyActivities', params: { tab: 'list' } }"
              >
                {{ $t('Sidebar.study.study_activities') }}
              </router-link>
            </td>
          </tr>
          <template v-for="arm in arms">
            <template v-if="arm.arm_connected_branch_arms?.length">
              <tr
                v-for="(branchArm, index) in arm.arm_connected_branch_arms"
                :key="branchArm.branch_arm_uid"
              >
                <td
                  v-if="index === 0"
                  :rowspan="arm.arm_connected_branch_arms.length"
                >
                  <div class="text-body-large line-height-125">
                    {{ arm.name }}
                  </div>
                  <div v-if="arm.number_of_subjects != null" class="text-body-medium text-gray">
                    {{ arm.number_of_subjects }}
                    {{ $t('StudyStructureOverview.subjects') }}
                  </div>
                </td>
                <td>
                  <div class="text-body-large line-height-125">
                    {{ branchArm.name }}
                  </div>
                  <div v-if="branchArm.number_of_subjects != null" class="text-body-medium text-gray">
                    {{ branchArm.number_of_subjects }}
                    {{ $t('StudyStructureOverview.subjects') }}
                  </div>
                </td>
                <StudyStructureCohortCell
                  :cohorts="cohorts"
                  :arm-uid="arm.arm_uid"
                  :branch-uid="branchArm.branch_arm_uid"
                />
                <td
                  v-for="studyEpoch in visibleStudyEpochs"
                  :key="`${studyEpoch.uid}-${branchArm.branch_arm_uid}`"
                >
                  {{
                    getDesignCellByBranch(
                      studyEpoch.uid,
                      branchArm.branch_arm_uid
                    )
                  }}
                </td>
              </tr>
            </template>
            <template v-else>
              <tr :key="arm.arm_uid">
                <td>
                  <div class="text-body-large line-height-125">
                    {{ arm.name }}
                  </div>
                  <div v-if="arm.number_of_subjects != null" class="text-body-medium text-gray">
                    {{ arm.number_of_subjects }}
                    {{ $t('StudyStructureOverview.subjects') }}
                  </div>
                </td>
                <td />
                <StudyStructureCohortCell :cohorts="cohorts" :arm-uid="arm.arm_uid" />
                <td
                  v-for="studyEpoch in visibleStudyEpochs"
                  :key="`${studyEpoch.uid}-${arm.arm_uid}`"
                >
                  {{ getDesignCellByArm(studyEpoch.uid, arm.arm_uid) }}
                </td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </template>
  </div>
</template>

<script setup>
import StudyStructureCohortCell from './StudyStructureCohortCell.vue'
import armsApi from '@/api/arms'
import cohortsApi from '@/api/cohorts'
import visitConstants from '@/constants/visits'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import studyEpochsApi from '@/api/studyEpochs'
import { onMounted, ref, computed } from 'vue'
import studyApi from '@/api/study'
import { studyPopulationTarget } from '@/utils/studyPopulationTarget'

const studiesGeneralStore = useStudiesGeneralStore()
const studyUid = studiesGeneralStore.selectedStudy.uid

const arms = ref([])
const branches = computed(() => arms.value.flatMap(arm => arm.arm_connected_branch_arms || []))
const cells = ref([])
const designLoading = ref(true)
const designError = ref(false)
const elementCount = ref(null)
const elementsLoading = ref(true)
const cohorts = ref([])
const designClass = ref('')
const sourceVariable = ref({})
const population = ref(null)
const populationLoading = ref(true)
const populationError = ref(false)

const studyEpochs = ref([])
const plannedNumberOfSubjects = computed(() =>
  studyPopulationTarget(population.value, arms.value)
)
const visibleStudyEpochs = computed(() => {
  return studyEpochs.value.filter(
    (studyEpoch) => studyEpoch.epoch_name !== visitConstants.EPOCH_BASIC
  )
})

onMounted(() => {
  armsApi
    .getStudyElements(studyUid, {
      page_size: 1,
      total_count: true,
    })
    .then(({ data }) => {
      if (Number.isSafeInteger(data.total) && data.total >= 0) {
        elementCount.value = data.total
      }
    })
    .catch(() => {
      elementCount.value = null
    })
    .finally(() => {
      elementsLoading.value = false
    })
  studyApi
    .getStudyPopulationMetadata(studyUid)
    .then((response) => {
      population.value = response.data.current_metadata.study_population
    })
    .catch(() => {
      populationError.value = true
    })
    .finally(() => {
      populationLoading.value = false
    })
  loadDesign()
  cohortsApi.getStudyDesignClass(studyUid)
    .then(({ data }) => { designClass.value = data.value })
    .catch(error => { designClass.value = error.response?.status === 404 ? '' : 'Unavailable' })
  cohortsApi.getSourceVariable(studyUid)
    .then(({ data }) => { sourceVariable.value = data || {} })
    .catch(() => { sourceVariable.value = {} })
})

async function loadDesign() {
  designLoading.value = true
  designError.value = false
  const params = { page_size: 0 }
  try {
    const [armRows, cohortRows, epochRows, cellRows] = await Promise.all([
      armsApi.getAllForStudy(studyUid, { params }),
      armsApi.getAllCohorts(studyUid, params),
      studyEpochsApi.getStudyEpochs(studyUid),
      armsApi.getAllStudyCells(studyUid),
    ])
    arms.value = armRows.data.items
    cohorts.value = cohortRows.data.items
    studyEpochs.value = epochRows.data.items
    cells.value = cellRows.data
  } catch {
    designError.value = true
  } finally {
    designLoading.value = false
  }
}

function getDesignCellByBranch(studyEpochUid, branchArmUid) {
  try {
    const result = cells.value.find(
      (cell) =>
        cell.study_epoch_uid === studyEpochUid &&
        cell.study_branch_arm_uid === branchArmUid
    )
    if (result) {
      return result.study_element_name
    }
    return ''
  } catch (error) {
    console.error(error)
  }
}

function getDesignCellByArm(studyEpochUid, armUid) {
  try {
    const result = cells.value.find(
      (cell) =>
        cell.study_epoch_uid === studyEpochUid && cell.study_arm_uid === armUid
    )
    if (result) {
      return result.study_element_name
    }
    return ''
  } catch (error) {
    console.error(error)
  }
}

</script>

<style scoped lang="scss">
table {
  width: 100%;
  text-align: left;
  border-spacing: 0px;
  border-collapse: separate;
  overflow: hidden;
}
tr {
  padding: 4px;
}
td,
th {
  border: 1px solid gray;
  border-top: none;
  padding-inline: 20px;
  padding-block: 6px;
  color: rgb(var(--v-theme-nnTrueBlue));
}
table {
  border-collapse: separate;
  border: solid gray 1px;
  border-radius: 10px;
}

td:last-child,
th:last-child {
  border-right: none;
}
thead > tr {
  background-color: rgb(var(--v-theme-nnBaseLight));
  color: rgb(var(--v-theme-nnTrueBlue));
  font-weight: 700;
}
thead > tr:first-of-type {
  background-color: rgb(var(--v-theme-nnTrueBlue));
}
.line-height-125 {
  line-height: 1.25;
}
</style>

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
      <v-btn variant="text" @click="loadDesign">{{
        $t('_global.retry')
      }}</v-btn>
    </v-alert>
    <template v-else>
      <dl class="design-metrics">
        <div>
          <dt>{{ $t('StudyStructureOverview.arms') }}</dt>
          <dd>{{ arms.length }}</dd>
        </div>
        <div>
          <dt>{{ $t('StudyStructureOverview.branch_arms') }}</dt>
          <dd>{{ branches.length }}</dd>
        </div>
        <div>
          <dt>{{ $t('StudyStructureOverview.cohorts') }}</dt>
          <dd>{{ cohortCount ?? $t('Workspace.unavailable') }}</dd>
        </div>
        <div>
          <dt>{{ $t('Workspace.elements') }}</dt>
          <dd>
            {{
              elementsLoading
                ? '…'
                : (elementCount ?? $t('Workspace.unavailable'))
            }}
          </dd>
        </div>
        <div>
          <dt>{{ $t('Workspace.enrollment_target') }}</dt>
          <dd>
            {{
              populationLoading
                ? '…'
                : populationError
                  ? $t('Workspace.unavailable')
                  : (plannedNumberOfSubjects ?? $t('Workspace.not_configured'))
            }}
          </dd>
        </div>
      </dl>
      <details class="design-details" @toggle="openDetails">
        <summary>{{ $t('Workspace.design_details') }}</summary>
        <v-progress-linear v-if="detailStatus === 'loading'" indeterminate />
        <v-alert v-if="detailStatus === 'error'" type="warning" variant="tonal">
          {{ $t('StudyStructureOverview.design_load_failed') }}
          <v-btn variant="text" @click="loadDetails">{{
            $t('_global.retry')
          }}</v-btn>
        </v-alert>
        <template v-if="detailStatus === 'ready'">
          <v-row>
            <v-col cols="4">
              <div class="text-body-large line-height-125">
                {{ $t('StudyStructureOverview.study_design_class') }}
              </div>
              <div class="text-headline-small font-weight-bold mt-1">
                {{
                  designClass === null
                    ? $t('Workspace.unavailable')
                    : designClass || '—'
                }}
              </div>
            </v-col>
            <v-col
              v-if="sourceVariable === null || sourceVariable.source_variable"
              cols="2"
            >
              <div class="text-body-large line-height-125">
                {{ $t('StudyStructureOverview.source_var') }}
              </div>
              <div class="text-headline-small font-weight-bold mt-1">
                {{
                  sourceVariable?.source_variable ?? $t('Workspace.unavailable')
                }}
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
                <td
                  v-for="studyEpoch in visibleStudyEpochs"
                  :key="studyEpoch.uid"
                >
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
                      <div
                        v-if="arm.number_of_subjects != null"
                        class="text-body-medium text-gray"
                      >
                        {{ arm.number_of_subjects }}
                        {{ $t('StudyStructureOverview.subjects') }}
                      </div>
                    </td>
                    <td>
                      <div class="text-body-large line-height-125">
                        {{ branchArm.name }}
                      </div>
                      <div
                        v-if="branchArm.number_of_subjects != null"
                        class="text-body-medium text-gray"
                      >
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
                      <div
                        v-if="arm.number_of_subjects != null"
                        class="text-body-medium text-gray"
                      >
                        {{ arm.number_of_subjects }}
                        {{ $t('StudyStructureOverview.subjects') }}
                      </div>
                    </td>
                    <td />
                    <StudyStructureCohortCell
                      :cohorts="cohorts"
                      :arm-uid="arm.arm_uid"
                    />
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
      </details>
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
const branches = computed(() =>
  arms.value.flatMap((arm) => arm.arm_connected_branch_arms || [])
)
const cells = ref([])
const designLoading = ref(true)
const designError = ref(false)
const elementCount = ref(null)
const elementsLoading = ref(true)
const cohorts = ref([])
const cohortCount = ref(null)
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
})

const detailStatus = ref('idle')
function openDetails(event) {
  if (event.currentTarget.open && detailStatus.value === 'idle') loadDetails()
}
async function loadDetails() {
  detailStatus.value = 'loading'
  try {
    const [epochs, designCells, cohortRows, classification, source] =
      await Promise.all([
        studyEpochsApi.getStudyEpochs(studyUid),
        armsApi.getAllStudyCells(studyUid),
        armsApi.getAllCohorts(studyUid, { page_size: 0 }),
        cohortsApi.getStudyDesignClass(studyUid).catch((error) => {
          if (error.response?.status === 404) return { data: { value: '' } }
          return null
        }),
        cohortsApi.getSourceVariable(studyUid).catch((error) => {
          if (error.response?.status === 404) return { data: {} }
          return null
        }),
      ])
    studyEpochs.value = epochs.data.items
    cells.value = designCells.data
    cohorts.value = cohortRows.data.items
    designClass.value = classification?.data.value ?? null
    sourceVariable.value = source?.data ?? null
    detailStatus.value = 'ready'
  } catch {
    detailStatus.value = 'error'
  }
}

async function loadDesign() {
  designLoading.value = true
  designError.value = false
  const params = { page_size: 0 }
  try {
    const [armRows, cohortRows] = await Promise.all([
      armsApi.getAllForStudy(studyUid, { params }),
      armsApi.getAllCohorts(studyUid, { page_size: 1, total_count: true }),
    ])
    arms.value = armRows.data.items
    const count = cohortRows.data.total
    cohortCount.value = Number.isSafeInteger(count) && count >= 0 ? count : null
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
.design-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 8rem), 1fr));
  gap: 1px;
  background: #cbd5df;
  border: 1px solid #cbd5df;
  border-radius: 7px;
  overflow: hidden;
}
.design-metrics div {
  padding: 8px 12px;
  background: white;
}
.design-metrics dt {
  font-size: 0.86rem;
  color: #425366;
}
.design-metrics dd {
  margin: 3px 0 0;
  font-size: 1.3rem;
  font-weight: 700;
}
.design-details {
  margin-top: 12px;
}
.design-details summary {
  cursor: pointer;
  font-weight: 600;
  padding: 8px 0;
}

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

<template>
  <div>
    <div class="d-flex page-title">
      {{ $t('StudyVisitForm.study_visit') + ': ' + visit.visit_name }}
      <v-spacer />
      <v-btn
        size="small"
        :title="$t('_global.close')"
        class="ml-2"
        icon="mdi-close"
        variant="text"
        @click="close"
      />
    </div>
    <v-alert v-if="readError" type="warning" variant="tonal">{{
      $t('Workspace.detail_unavailable')
    }}</v-alert>
    <v-card elevation="0" class="rounded-0">
      <v-card-text>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.vtype_step_label') }}
          </v-col>
          <v-col cols="2">
            {{
              visit.visit_class
                ? (visitClasses.find((cl) => cl.value === visit.visit_class)
                    ?.label ?? visit.visit_class)
                : '—'
            }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.period') }}
          </v-col>
          <v-col cols="2">
            {{ epoch?.epoch_name || visit.study_epoch_uid || '—' }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.visit_short_name') }}
          </v-col>
          <v-col cols="2">
            {{ visit.visit_short_name }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.visit_type') }}
          </v-col>
          <v-col cols="2">
            {{ visit.visit_type_name }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.contact_mode') }}
          </v-col>
          <v-col cols="2">
            {{
              visit.visit_contact_mode?.sponsor_preferred_name ||
              visit.visit_contact_mode_name ||
              (isUntimedVisit(visit)
                ? $t('StudyVisitForm.untimed_contact_unspecified')
                : '')
            }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.anchor_visit') }}
          </v-col>
          <v-col cols="2">
            {{ $filters.yesno(visit.is_global_anchor_visit) }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.time_reference') }}
          </v-col>
          <v-col cols="2">
            {{ visit.time_reference_name }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.time_reference') }}
          </v-col>
          <v-col cols="2">
            {{ visit.visit_sublabel_reference }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.time_dist') }}
          </v-col>
          <v-col cols="2">
            {{
              isUntimedVisit(visit)
                ? untimedTimingText(visit, $t)
                : visit.time_value + ' ' + visit.time_unit_name
            }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.study_day_label') }}
          </v-col>
          <v-col cols="2">
            {{ visit.study_day_label }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.study_week_label') }}
          </v-col>
          <v-col cols="2">
            {{ visit.study_week_label }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.visit_window') }}
          </v-col>
          <v-col cols="2">
            <template v-if="isUntimedVisit(visit)">{{
              $t('StudyVisitForm.untimed_unknown_window')
            }}</template>
            <template v-else>
              {{ visit.min_visit_window_value }} /
              {{ visit.max_visit_window_value }}
              {{ visit.visit_window_unit_name }}
            </template>
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.visit_description') }}
          </v-col>
          <v-col cols="2">
            {{ visit.description }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.epoch_allocation') }}
          </v-col>
          <v-col cols="2">
            {{ visit.epoch_allocation_name }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.visit_start_rule') }}
          </v-col>
          <v-col cols="2">
            {{ visit.start_rule }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.visit_stop_rule') }}
          </v-col>
          <v-col cols="2">
            {{ visit.end_rule }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.visit_notes') }}
          </v-col>
          <v-col cols="2">
            {{ visit.note }}
          </v-col>
        </v-row>
        <v-row>
          <v-col cols="2" class="font-weight-bold">
            {{ $t('StudyVisitForm.show_visit') }}
          </v-col>
          <v-col cols="2">
            {{ $filters.yesno(visit.show_visit) }}
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>
  </div>
</template>

<script>
import studyEpochs from '@/api/studyEpochs'
import { isUntimedVisit, untimedTimingText } from '@/utils/visitTiming'
import visitConstants from '@/constants/visits'

export default {
  setup() {
    return {
      isUntimedVisit,
      untimedTimingText,
    }
  },
  data() {
    return {
      visit: {},
      readError: false,
      visitClasses: [
        {
          label: this.$t('StudyVisitForm.scheduled_visit'),
          value: visitConstants.CLASS_SINGLE_VISIT,
        },
        {
          label: this.$t('StudyVisitForm.unscheduled_visit'),
          value: visitConstants.CLASS_UNSCHEDULED_VISIT,
        },
        {
          label: this.$t('StudyVisitForm.non_visit'),
          value: visitConstants.CLASS_NON_VISIT,
        },
        {
          label: this.$t('StudyVisitForm.special_visit'),
          value: visitConstants.CLASS_SPECIAL_VISIT,
        },
        {
          label: this.$t('StudyVisitForm.insertion_visit'),
          value: visitConstants.CLASS_INSERTION_VISIT,
        },
      ],
      epoch: null,
    }
  },
  mounted() {
    const uid = this.$route.params.study_id
    studyEpochs
      .getStudyVisit(uid, this.$route.params.id)
      .then(async ({ data }) => {
        this.visit = data
        if (data.study_epoch_uid)
          this.epoch = (
            await studyEpochs.getStudyEpoch(uid, data.study_epoch_uid)
          ).data
      })
      .catch(() => {
        this.readError = true
      })
  },
  methods: {
    close() {
      this.$router.push({ name: 'StudyStructure', params: { tab: 'visits' } })
    },
  },
}
</script>

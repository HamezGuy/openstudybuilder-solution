export function isUntimedVisit(visit) {
  return visit?.timing_mode === 'UNTIMED'
}

export function untimedTimingText(visit, translate, visits = []) {
  const timing = visit?.untimed_timing
  if (!timing) return ''
  const anchor =
    visits.find((candidate) => candidate.uid === timing.anchor_visit_uid)
      ?.visit_name || timing.anchor_visit_uid
  if (timing.kind === 'manual_date') {
    return translate(
      timing.repeating
        ? 'StudyVisitForm.untimed_manual_repeating'
        : 'StudyVisitForm.untimed_manual_once'
    )
  }
  if (timing.kind === 'event_relative') {
    return translate('StudyVisitForm.untimed_relative_summary', {
      days: timing.nominal_offset_days,
      anchor,
    })
  }
  if (timing.kind === 'calendar_repeat') {
    return translate('StudyVisitForm.untimed_calendar_summary', {
      interval: timing.interval_months,
      first: timing.first_occurrence,
      last: timing.last_occurrence,
      anchor,
    })
  }
  return ''
}

export function prepareVisitTimingPayload(input) {
  const data = JSON.parse(JSON.stringify(input))
  if (!isUntimedVisit(data)) return data
  for (const field of [
    'time_reference',
    'time_value',
    'time_unit_uid',
    'min_visit_window_value',
    'max_visit_window_value',
    'visit_window_unit_uid',
    'visit_sublabel_reference',
    'repeating_frequency_uid',
  ]) {
    data[field] = null
  }
  delete data.time_unit
  if (!data.visit_contact_mode?.term_uid) data.visit_contact_mode = null
  data.is_global_anchor_visit = false
  data.visit_subclass = 'SINGLE_VISIT'
  for (const field of ['visit_number', 'unique_visit_number']) {
    if (
      data[field] !== null &&
      data[field] !== undefined &&
      data[field] !== ''
    ) {
      data[field] = Number(data[field])
    }
  }
  for (const field of [
    'nominal_offset_days',
    'interval_months',
    'first_occurrence',
    'last_occurrence',
  ]) {
    const value = data.untimed_timing?.[field]
    if (value !== undefined && value !== null && value !== '') {
      data.untimed_timing[field] = Number(value)
    }
  }
  return data
}

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  isUntimedVisit,
  prepareVisitTimingPayload,
  untimedTimingText,
} from '../src/utils/visitTiming.js'

const messages = JSON.parse(
  readFileSync(new URL('../src/locales/en/app.json', import.meta.url))
)
const translate = (key, args = {}) => {
  const text = key.split('.').reduce((value, part) => value[part], messages)
  return text.replace(/\{(\w+)\}/g, (_match, name) => args[name])
}

test('explicit untimed payload keeps source rules and clears fixed timing only', () => {
  const source = {
    timing_mode: 'UNTIMED',
    visit_name: 'Nominal first-dose follow-up',
    visit_short_name: 'D15',
    visit_number: '4',
    unique_visit_number: '400',
    time_value: 0,
    time_unit: { uid: 'day' },
    time_unit_uid: 'day',
    time_reference: { term_uid: 'global' },
    min_visit_window_value: 0,
    max_visit_window_value: 0,
    visit_window_unit_uid: 'day',
    visit_contact_mode: null,
    description: 'Exact source description',
    start_rule: 'Nominal Day 15',
    end_rule: 'Actual date recorded manually',
    untimed_timing: {
      kind: 'event_relative',
      anchor_visit_uid: 'Dose1',
      nominal_offset_days: '15',
    },
  }
  const before = structuredClone(source)
  const result = prepareVisitTimingPayload(source)
  assert.deepEqual(source, before)
  assert.equal(result.untimed_timing.nominal_offset_days, 15)
  assert.equal(result.untimed_timing.anchor_visit_uid, 'Dose1')
  assert.equal(result.visit_number, 4)
  assert.equal(result.unique_visit_number, 400)
  assert.equal(result.visit_contact_mode, null)
  assert.equal(result.start_rule, source.start_rule)
  assert.equal(result.end_rule, source.end_rule)
  for (const field of [
    'time_value',
    'time_unit_uid',
    'time_reference',
    'min_visit_window_value',
    'max_visit_window_value',
    'visit_window_unit_uid',
  ]) {
    assert.equal(result[field], null)
  }
  assert.ok(!('time_unit' in result))
})

test('calendar recurrence remains calendar months with exact bounds', () => {
  const source = {
    timing_mode: 'UNTIMED',
    untimed_timing: {
      kind: 'calendar_repeat',
      anchor_visit_uid: 'Last',
      interval_months: '1',
      first_occurrence: '1',
      last_occurrence: '12',
    },
  }
  const payload = prepareVisitTimingPayload(source)
  assert.deepEqual(payload.untimed_timing, {
    kind: 'calendar_repeat',
    anchor_visit_uid: 'Last',
    interval_months: 1,
    first_occurrence: 1,
    last_occurrence: 12,
  })
  const text = untimedTimingText(payload, translate, [
    { uid: 'Last', visit_name: 'Last actual dose' },
  ])
  assert.match(text, /calendar month/)
  assert.match(text, /1–12/)
  assert.match(text, /Last actual dose/)
  assert.doesNotMatch(text, /30|360|365/)
})

test('missing nominal quantities and repeatability are never invented', () => {
  const relative = prepareVisitTimingPayload({
    timing_mode: 'UNTIMED',
    untimed_timing: {
      kind: 'event_relative',
      anchor_visit_uid: 'Dose',
      nominal_offset_days: '',
    },
  })
  assert.equal(relative.untimed_timing.nominal_offset_days, '')
  const manual = prepareVisitTimingPayload({
    timing_mode: 'UNTIMED',
    untimed_timing: { kind: 'manual_date' },
  })
  assert.ok(!('repeating' in manual.untimed_timing))
})

test('standard visits retain their existing payload unchanged', () => {
  const standard = {
    timing_mode: 'STANDARD',
    time_value: 0,
    time_unit_uid: 'days',
    time_reference: { term_uid: 'anchor' },
    min_visit_window_value: -9999,
    max_visit_window_value: 9999,
    visit_contact_mode: { term_uid: 'provided-mode' },
  }
  assert.deepEqual(prepareVisitTimingPayload(standard), standard)
  assert.equal(isUntimedVisit(standard), false)
})

test('only explicit untimed mode turns an unselected contact into null', () => {
  for (const mode of [null, {}, { term_uid: null }]) {
    assert.equal(
      prepareVisitTimingPayload({
        timing_mode: 'UNTIMED',
        visit_contact_mode: mode,
      }).visit_contact_mode,
      null
    )
  }
  const contact = {
    term_uid: 'source-supported-phone',
    sponsor_preferred_name: 'Phone Contact',
  }
  assert.deepEqual(
    prepareVisitTimingPayload({
      timing_mode: 'UNTIMED',
      visit_contact_mode: contact,
    }).visit_contact_mode,
    contact
  )
  assert.deepEqual(
    prepareVisitTimingPayload({
      timing_mode: 'STANDARD',
      visit_contact_mode: {},
    }).visit_contact_mode,
    {}
  )
})

test('nominal day display uses the exact event anchor and never an enrollment day', () => {
  const visit = {
    timing_mode: 'UNTIMED',
    untimed_timing: {
      kind: 'event_relative',
      anchor_visit_uid: 'Dose1',
      nominal_offset_days: 15,
    },
  }
  const text = untimedTimingText(visit, translate, [
    { uid: 'Dose1', visit_name: 'First actual dose' },
  ])
  assert.match(text, /Nominal 15 days relative to First actual dose/)
  assert.match(text, /manual date/)
  assert.doesNotMatch(text, /enrollment|study day|Day 16/i)
})

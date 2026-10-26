import assert from 'node:assert/strict'
import { test } from 'node:test'
import { studyTemplateUid } from '../src/utils/studyTemplate.js'

test('a configured template offers its study uid', () => {
  assert.equal(
    studyTemplateUid({
      uid: 'StudyTemplate_000001',
      study_uid: 'Study_000001',
    }),
    'Study_000001'
  )
})

test('no template, or a refused template read, offers nothing', () => {
  // null: GET studies/template with no template set.
  // {}: the form's initial value, kept when the strict profile answers 403.
  for (const template of [null, undefined, {}, { study_uid: '' }]) {
    assert.equal(studyTemplateUid(template), '')
  }
  assert.equal(studyTemplateUid({ study_uid: '   ' }), '')
  assert.equal(studyTemplateUid({ study_uid: 42 }), '')
})

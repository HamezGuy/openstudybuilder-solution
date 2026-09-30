import test from 'node:test'
import assert from 'node:assert/strict'
import { studyPopulationTarget } from '../src/utils/studyPopulationTarget.js'

test('observational studies use their population target without treatment arms', () => {
  assert.equal(studyPopulationTarget({ number_of_expected_subjects: 30000 }, []), 30000)
})

test('the study target takes precedence over partial arm allocation and retains zero', () => {
  assert.equal(studyPopulationTarget({ number_of_expected_subjects: 30000 }, [{ number_of_subjects: 100 }]), 30000)
  assert.equal(studyPopulationTarget({ number_of_expected_subjects: 0 }, [{ number_of_subjects: 100 }]), 0)
})

test('missing or partly configured arm enrollment never becomes a zero target', () => {
  assert.equal(studyPopulationTarget({}, []), null)
  assert.equal(studyPopulationTarget({}, [{ number_of_subjects: 100 }, { number_of_subjects: null }]), null)
  assert.equal(studyPopulationTarget({ number_of_expected_subjects: -1 }), null)
})

test('legacy complete arm allocation is retained unless an explicit missing-value reason exists', () => {
  const arms = [{ number_of_subjects: 100 }, { number_of_subjects: 200 }]
  assert.equal(studyPopulationTarget({}, arms), 300)
  assert.equal(studyPopulationTarget({ number_of_expected_subjects_null_value_code: { term_uid: 'unknown' } }, arms), null)
})

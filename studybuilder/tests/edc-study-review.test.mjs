import assert from 'node:assert/strict'
import { test } from 'node:test'
import { edcStudyReview } from '../src/utils/edcStudyReview.js'

const fixture = () => ({
  formatVersion: '2.0',
  profile: { id: 'edc-study-exchange/2', modelVersion: '4.0.0', mode: 'draft' },
  definition: {
    selection: { versionId: 'v2', designId: 'design-2' },
    document: {
      study: {
        id: 'study', name: 'Synthetic complete study', instanceType: 'Study',
        versions: [
          { id: 'v1', instanceType: 'StudyVersion', studyDesigns: [{ id: 'design-1', name: 'Old design' }] },
          { id: 'v2', instanceType: 'StudyVersion', versionIdentifier: '2.0',
            studyDesigns: [{
              id: 'design-2', name: 'Selected design', instanceType: 'InterventionalStudyDesign',
              population: { id: 'population', instanceType: 'StudyDesignPopulation', cohorts: [{ id: 'cohort', instanceType: 'StudyCohort' }] },
              objectives: [{ id: 'objective', instanceType: 'Objective' }],
              estimands: [{ id: 'estimand', instanceType: 'Estimand' }],
            }] },
        ],
      },
    },
  },
  execution: {
    visits: [{ refKey: 'visit' }],
    forms: { forms: [{ fields: [{ refKey: 'zero', defaultValue: 0 }] }] },
    visitFormAssignments: [{ visitRef: 'visit', formRef: 'form' }],
  },
  extensions: {
    _osbExport: {
      census: { rows: [{ kind: 'native_study_collection', ref: 'studyCohort' }] },
      mappingReport: { state: 'incomplete', issues: [{ code: 'UNKNOWN_REQUIRED_FACT', sourcePath: '/population' }] },
      mappingAuthority: { authoritative: false, mode: 'shadow' },
    },
  },
})

test('reviews the actual V2 shape and exact selected version/design with full entity counts', () => {
  const bundle = fixture(), before = structuredClone(bundle)
  const result = edcStudyReview(bundle)
  assert.equal(result.name, 'Synthetic complete study')
  assert.equal(result.version, '2.0')
  assert.equal(result.design, 'Selected design')
  assert.equal(result.counts.fields, 1)
  assert.equal(result.counts.versions, 2)
  assert.equal(result.counts.designs, 2)
  assert(result.entities.some((item) => item.name === 'StudyCohort' && item.count === 1))
  assert(result.entities.some((item) => item.name === 'Estimand' && item.count === 1))
  assert.equal(result.mappingReport.issues[0].code, 'UNKNOWN_REQUIRED_FACT')
  assert.equal(result.censusRows[0].ref, 'studyCohort')
  assert.deepEqual(bundle, before)
})

test('does not select the first version/design when the source selection is missing or stale', () => {
  const bundle = fixture()
  bundle.definition.selection = { versionId: null, designId: null }
  let result = edcStudyReview(bundle)
  assert.equal(result.selectionComplete, false)
  assert.equal(result.version, null)
  assert.equal(result.design, null)
  bundle.definition.selection = { versionId: 'v2', designId: 'design-1' }
  result = edcStudyReview(bundle)
  assert.equal(result.selectionComplete, false)
  assert.equal(result.version, '2.0')
  assert.equal(result.design, null)
})

test('rejects a retired or malformed response instead of showing stale or invented counts', () => {
  for (const bundle of [
    { formatVersion: '1.0', study: { name: 'Old' }, visits: [], forms: { forms: [] } },
    { ...fixture(), execution: {} },
    { ...fixture(), profile: { id: 'wrong', modelVersion: '4.0.0' } },
    null,
  ]) assert.throws(() => edcStudyReview(bundle), /study exchange V2/)
})

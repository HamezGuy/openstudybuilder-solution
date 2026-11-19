import assert from 'node:assert/strict'
import test from 'node:test'
import {
  countResource,
  loadWorkingCounts,
  readFailure,
  reviewSnapshot,
  workingResources,
} from '../src/utils/edcReleaseReview.js'

const uid = 'Study_test'
const snapshot = {
  schema_version: 'osb-authority/1.2',
  mapping_authority: 'OpenStudyBuilder',
  study_uid: uid,
  study_version: null,
  authority_mode: 'enforced',
  release_eligible: true,
  blockers: [],
  counts: { arms: 2 },
  content_hash: 'a'.repeat(64),
}
const error = (status, message) => ({ response: { status, data: { message } } })

test('accepts only the exact study and requested version; eligibility agrees with blockers', () => {
  assert.equal(reviewSnapshot(snapshot, uid, 0).state, 'eligible')
  const blocked = {
    code: 'ODM_REQUIRED',
    path: '/odm',
    detail: 'Select an ODM definition.',
  }
  const held = {
    ...snapshot,
    study_version: '1.0',
    release_eligible: false,
    blockers: [blocked],
  }
  assert.deepEqual(reviewSnapshot(held, uid, '1.0').blockers, [blocked])
  for (const changed of [
    { study_uid: 'another' },
    { study_version: '1.0' },
    { release_eligible: false },
    { blockers: [blocked] },
    { content_hash: 'wrong' },
    { counts: { arms: -1 } },
    { authority_mode: 'unconfigured' },
    { schema_version: 'new-shape' },
  ])
    assert.throws(() => reviewSnapshot({ ...snapshot, ...changed }, uid, 0))
  assert.equal(
    reviewSnapshot({ ...snapshot, authority_mode: 'legacy' }, uid, 0).mode,
    'legacy'
  )
})

test('handles native source decisions, access and unrelated API failures separately', () => {
  const msg =
    'USDM_ARM_DATA_ORIGIN_AUTHORITY_REQUIRED: study-arms/arm-1; select a documented origin.'
  const hold = readFailure(error(422, msg))
  assert.equal(hold.state, 'held')
  assert.deepEqual(hold.blockers, [
    {
      code: 'USDM_ARM_DATA_ORIGIN_AUTHORITY_REQUIRED',
      detail: 'study-arms/arm-1; select a documented origin.',
    },
  ])
  for (const [status, state] of [
    [401, 'signed-out'],
    [403, 'denied'],
    [404, 'unavailable'],
    [500, 'unavailable'],
  ]) {
    assert.equal(readFailure(error(status, msg)).state, state)
  }
  assert.equal(
    readFailure(error(422, 'The request is invalid.')).state,
    'unavailable'
  )
})

test('uses exact page totals and complete lists, never assumes a missing count or cross-study row', () => {
  const page = workingResources.find((r) => r.key === 'cohorts')
  const list = workingResources.find((r) => r.key === 'design_cells')
  assert.equal(
    countResource(page, { items: [{ study_uid: uid }], total: 3 }, uid),
    3
  )
  assert.equal(countResource(list, [], uid), 0)
  for (const value of [
    [],
    { items: [] },
    { items: [], total: -1 },
    { items: [{}], total: 0 },
    { items: [{ study_uid: 'other' }], total: 1 },
  ]) {
    assert.throws(() => countResource(page, value, uid))
  }
  assert.throws(() => countResource(list, { items: [], total: 0 }, uid))
})

test('a held snapshot retains separately authorized counts, partial failures and the selected version', async () => {
  const asks = [],
    rows = {}
  const api = {
    async getWorkingResource(id, resource, version) {
      asks.push([id, resource.key, version])
      if (resource.key === 'criteria') throw error(403, 'denied')
      if (resource.key === 'objectives') throw error(503, 'unavailable')
      return { data: resource.list ? [{}, {}] : { items: [{}], total: 6 } }
    },
  }
  const read = (result, controller = new AbortController()) =>
    loadWorkingCounts(
      api,
      uid,
      '1.0',
      result,
      controller.signal,
      (key, value) => {
        rows[key] = value
      }
    )
  await read({ state: 'held', counts: { arms: 2 } })
  assert.deepEqual(rows.cohorts, { count: 6 })
  assert.deepEqual(rows.design_cells, { count: 2 })
  assert.deepEqual(rows.criteria, { state: 'denied' })
  assert.deepEqual(rows.objectives, { state: 'unavailable' })
  assert.equal(asks.length, workingResources.length - 1)
  assert(
    asks.every(
      ([id, key, version]) => id === uid && key !== 'arms' && version === '1.0'
    )
  )
  asks.length = 0
  await read({ state: 'denied' })
  await read({ state: 'signed-out' })
  assert.equal(asks.length, 0)
})

test('aborting a switched study discards in-flight rows and stops starting reads', async () => {
  const controller = new AbortController(),
    rows = []
  const api = {
    async getWorkingResource() {
      controller.abort()
      return { data: { items: [], total: 0 } }
    },
  }
  await loadWorkingCounts(
    api,
    uid,
    null,
    { state: 'held' },
    controller.signal,
    (key) => rows.push(key)
  )
  assert.deepEqual(rows, [])
})

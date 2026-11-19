import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, nextTick, ref } from 'vue'
import { useStudyListing } from '../../src/composables/useStudyListing.js'

test('a failed listing can retry the same page and never shows a late response from another study version', async () => {
  const version = ref(null)
  const requests = []
  const scope = effectScope()
  const listing = scope.run(() =>
    useStudyListing(
      () => ['study-A', version.value, 'TA'],
      (uid, type, params) =>
        new Promise((resolve, reject) =>
          requests.push({ uid, type, params, resolve, reject })
        )
    )
  )
  try {
    const first = listing.load({ page_number: 2 })
    version.value = '1'
    await nextTick()
    assert.deepEqual(requests[1].params, {
      page_number: 2,
      study_value_version: '1',
    })
    requests[1].reject({
      response: { data: { message: 'Study identifier missing' } },
    })
    await nextTick()
    requests[0].resolve({ data: { items: ['old'], total: 1 } })
    await first
    assert.equal(listing.listingError.value, 'Study identifier missing')
    assert.deepEqual(listing.items.value, [])
    const retry = listing.retryListing()
    assert.deepEqual(requests[2].params, requests[1].params)
    requests[2].resolve({ data: { items: ['working row'], total: 1 } })
    await retry
    assert.deepEqual(listing.items.value, ['working row'])
    assert.equal(listing.listingError.value, '')
  } finally {
    scope.stop()
  }
})

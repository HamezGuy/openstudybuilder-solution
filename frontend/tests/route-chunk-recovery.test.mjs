import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  clearRouteChunkRetry,
  retryRouteChunk,
} from '../src/utils/routeChunkRecovery.js'

test('an expired route chunk reloads the requested page once and preserves its query and fragment', () => {
  const storage = new Map()
  const navigations = []
  const browser = {
    location: {
      origin: 'https://study.example',
      replace: (url) => navigations.push(url),
    },
    navigator: { onLine: true },
    sessionStorage: {
      getItem: (key) => storage.get(key),
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
  }
  const target = '/studies/example/activities?version=1#schedule'
  const failure = new TypeError('Failed to fetch dynamically imported module')
  retryRouteChunk(failure, target, browser)
  clearRouteChunkRetry('/studies', browser)
  retryRouteChunk(failure, target, browser)
  assert.deepEqual(navigations, [browser.location.origin + target])
  clearRouteChunkRetry(target, browser)
  assert.equal(storage.size, 0)
  retryRouteChunk(new Error('Study access required'), target, browser)
  retryRouteChunk(failure, 'https://other.example/', browser)
  browser.navigator.onLine = false
  retryRouteChunk(failure, target, browser)
  assert.equal(navigations.length, 1)
})

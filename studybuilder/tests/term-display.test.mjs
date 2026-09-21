import test from 'node:test'
import assert from 'node:assert/strict'
import { termDisplayName, hasMetadataValue } from '../src/utils/termDisplay.js'

test('native dictionary names are displayed in the population summary', () => {
  assert.equal(termDisplayName({ term_uid: 'DictionaryTerm_7', name: 'COVID-19' }), 'COVID-19')
  assert.equal(termDisplayName({ term_uid: 'CT_1', sponsor_preferred_name: 'Observational' }), 'Observational')
})

test('missing terminology retains its identity instead of a blank or exception', () => {
  assert.equal(termDisplayName(null), 'Not configured')
  assert.equal(termDisplayName({ term_uid: 'CT_1', sponsor_preferred_name: null, date_conflict: true }),
    'Term unavailable (CT_1)')
})

test('zero and false remain real metadata, while null and empty selections are missing', () => {
  for (const value of [0, false, 'COVID-19', [{ name: 'Observational' }]]) {
    assert.equal(hasMetadataValue(value), true)
  }
  for (const value of [null, undefined, '', '  ', []]) {
    assert.equal(hasMetadataValue(value), false)
  }
})

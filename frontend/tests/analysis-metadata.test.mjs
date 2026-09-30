import assert from 'node:assert/strict'
import test from 'node:test'
import {
  analysisMetadataRowKey,
  analysisMetadataValue,
} from '../src/utils/analysisMetadata.js'

test('source names and real zero values survive missing analysis configuration', () => {
  const item = { SOURCE_ACTIVITY_NAME: 'Symptoms', PARAMN: 0, PARAMCD: null }
  assert.equal(analysisMetadataValue(item, 'SOURCE_ACTIVITY_NAME'), 'Symptoms')
  assert.equal(analysisMetadataValue(item, 'PARAMN'), 0)
  assert.equal(analysisMetadataValue(item, 'PARAMCD'), 'Not configured')
  assert.equal(analysisMetadataValue({ FLAG: false }, 'FLAG'), false)
})

test('untimed day and week values are explicit without inventing numeric dates', () => {
  const item = { TIMING_MODE: 'UNTIMED', AVISIT1N: null, AVISIT2N: null }
  assert.equal(analysisMetadataValue(item, 'AVISIT1N'), 'Not fixed')
  assert.equal(analysisMetadataValue(item, 'AVISIT2N'), 'Not fixed')
  assert.equal(analysisMetadataValue(item, 'PARAMCD'), 'Not configured')
  assert.equal(analysisMetadataValue({ AVISIT1N: 0 }, 'AVISIT1N'), 0)
})

test('uncoded schedules and separate studies retain distinct row identities', () => {
  const base = {
    STUDYID_FLOWCHART: 'A',
    AVISITN: 100,
    PARAMN: 1,
    PARAMCD: null,
  }
  const first = { ...base, SOURCE_SCHEDULE_UID: 'schedule-1' }
  const second = { ...base, SOURCE_SCHEDULE_UID: 'schedule-2' }
  assert.notEqual(
    analysisMetadataRowKey('mdflow', first),
    analysisMetadataRowKey('mdflow', second)
  )
  assert.notEqual(
    analysisMetadataRowKey('mdflow', first),
    analysisMetadataRowKey('mdflow', { ...first, STUDYID_FLOWCHART: 'B' })
  )
  assert.equal(
    analysisMetadataRowKey('mdflow', first),
    analysisMetadataRowKey('mdflow', { ...first })
  )
})

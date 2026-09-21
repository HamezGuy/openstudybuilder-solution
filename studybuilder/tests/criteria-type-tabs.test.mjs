import test from 'node:test'
import assert from 'node:assert/strict'
import { criteriaTypeTabs } from '../src/utils/criteriaTypeTabs.js'

test('duplicate labels produce one tab without losing imported term identities', () => {
  const source = [
    {
      term_uid: 'local-inclusion',
      sponsor_preferred_name: 'Inclusion Criteria',
      order: 1,
    },
    {
      term_uid: 'source-inclusion',
      sponsor_preferred_name: 'Inclusion Criteria',
      order: 1,
    },
    {
      term_uid: 'source-exclusion',
      sponsor_preferred_name: 'Exclusion Criteria',
      order: 2,
    },
  ]
  const before = structuredClone(source)
  const tabs = criteriaTypeTabs(source)
  assert.equal(tabs.length, 2)
  assert.deepEqual(tabs[0].term_uids, ['local-inclusion', 'source-inclusion'])
  assert.deepEqual(tabs[1].term_uids, ['source-exclusion'])
  assert.deepEqual(source, before)
})

test('default tab follows configured terminology order instead of alphabetical response order', () => {
  const tabs = criteriaTypeTabs([
    { term_uid: 'dose', sponsor_preferred_name: 'Dosing Criteria', order: 5 },
    {
      term_uid: 'exclude',
      sponsor_preferred_name: 'Exclusion Criteria',
      order: 2,
    },
    {
      term_uid: 'include',
      sponsor_preferred_name: 'Inclusion Criteria',
      order: 1,
    },
  ])
  assert.deepEqual(
    tabs.map((row) => row.term_uid),
    ['include', 'exclude', 'dose']
  )
})

test('unranked categories remain available and repeated IDs do not duplicate history requests', () => {
  const tabs = criteriaTypeTabs([
    { term_uid: 'unranked', sponsor_preferred_name: 'Other', order: null },
    {
      term_uid: 'include',
      sponsor_preferred_name: 'Inclusion Criteria',
      order: 1,
    },
    {
      term_uid: 'include',
      sponsor_preferred_name: 'Inclusion Criteria',
      order: 1,
    },
  ])
  assert.equal(tabs[0].sponsor_preferred_name, 'Inclusion Criteria')
  assert.deepEqual(tabs[0].term_uids, ['include'])
  assert.equal(tabs[1].sponsor_preferred_name, 'Other')
  assert.deepEqual(criteriaTypeTabs([]), [])
})

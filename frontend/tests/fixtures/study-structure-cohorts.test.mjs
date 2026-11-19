import assert from 'node:assert/strict'
import test from 'node:test'
import { cohortsForStructureRow } from '../../src/utils/studyStructureCohorts.js'

test('each arm shows all its direct cohorts and only the selected branch cohorts', () => {
  const arm = (arm_uid) => ({ arm_uid })
  const rows = [
    { name: 'Both', arm_roots: [arm('a'), arm('b')] },
    { name: 'Another direct cohort', arm_roots: [arm('a')] },
    { name: 'Branch only', arm_roots: [arm('a')], branch_arm_roots: [{ branch_arm_uid: 'a1' }] },
    { name: 'Unlinked' },
  ]
  const names = (a, b) => cohortsForStructureRow(rows, a, b).map((row) => row.name)
  assert.deepEqual(names('a'), ['Both', 'Another direct cohort'])
  assert.deepEqual(names('a', 'a1'), ['Both', 'Another direct cohort', 'Branch only'])
  assert.deepEqual(names('a', 'a2'), ['Both', 'Another direct cohort'])
  assert.deepEqual(names('b'), ['Both'])
  assert.deepEqual(names('c'), [])
})

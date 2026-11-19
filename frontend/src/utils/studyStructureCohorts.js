export function cohortsForStructureRow(cohorts, armUid, branchUid) {
  return cohorts.filter((cohort) => {
    const branches = cohort.branch_arm_roots ?? []
    if (branches.length) {
      return Boolean(branchUid && branches.some((branch) => branch.branch_arm_uid === branchUid))
    }
    return cohort.arm_roots?.some((arm) => arm.arm_uid === armUid)
  })
}

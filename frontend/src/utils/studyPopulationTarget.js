const isCount = (value) => Number.isSafeInteger(value) && value >= 0

export function studyPopulationTarget(population, arms = []) {
  if (isCount(population?.number_of_expected_subjects)) {
    return population.number_of_expected_subjects
  }
  if (population?.number_of_expected_subjects_null_value_code) {
    return null
  }
  if (arms.length && arms.every((arm) => isCount(arm.number_of_subjects))) {
    const total = arms.reduce((sum, arm) => sum + arm.number_of_subjects, 0)
    return isCount(total) ? total : null
  }
  return null
}

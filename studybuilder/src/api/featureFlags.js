import repository from './repository'

const resource = 'feature-flags'

export default {
  get(params) {
    return repository.get(`${resource}`, { params })
  },
  update(featureFlagId, payload) {
    return repository.patch(`${resource}/${featureFlagId}`, payload)
  },
}

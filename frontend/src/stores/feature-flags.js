import { ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/api/featureFlags'

export const useFeatureFlagsStore = defineStore('featureFlags', () => {
  const featureFlags = ref({})

  let audience = null
  let lastRead = 0
  let pending = null

  function fetchFeatureFlags(identity, fresh = false) {
    if (audience !== identity) {
      audience = identity
      pending = null
      lastRead = 0
      featureFlags.value = {}
    }
    if (pending) return pending
    if (!fresh && Date.now() - lastRead < 60000) return Promise.resolve()
    const read = api
      .get()
      .then(({ data }) => {
        if (pending !== read) return
        featureFlags.value = Object.fromEntries(
          data.map((flag) => [flag.name, flag.enabled])
        )
        lastRead = Date.now()
      })
      .catch(() => {
        if (pending === read) {
          featureFlags.value = {}
          lastRead = 0
        }
      })
      .finally(() => {
        if (pending === read) pending = null
      })
    pending = read
    return read
  }

  function applyFlag(flag) {
    pending = null
    lastRead = 0
    featureFlags.value = {
      ...featureFlags.value,
      [flag.name]: flag.status === 'Final' && flag.enabled,
    }
  }

  function getFeatureFlag(name) {
    return featureFlags.value[name] || false
  }

  return { featureFlags, fetchFeatureFlags, applyFlag, getFeatureFlag }
})

import { ref, shallowRef, watch } from 'vue'

// Reuse the current filters for a retry. A late response from a previous
// study, version, or page must never replace the current listing.
export function useStudyListing(scope, request) {
  const items = shallowRef([])
  const total = ref(0)
  const error = ref('')
  let lastParams
  let revision = 0

  async function load(params) {
    lastParams = params
    const current = ++revision
    const [uid, version, type] = scope()
    error.value = ''
    if (!uid) return
    try {
      const { data } = await request(uid, type, {
        ...params,
        ...(version ? { study_value_version: version } : {}),
      })
      if (current !== revision) return
      items.value = data.items
      total.value = data.total
    } catch (cause) {
      if (current !== revision) return
      items.value = []
      total.value = 0
      error.value = cause.response?.data?.message || true
    }
  }

  watch(scope, () => {
    revision++
    items.value = []
    total.value = 0
    error.value = ''
    if (lastParams) load(lastParams)
  })
  return {
    items,
    total,
    listingError: error,
    load,
    retryListing: () => load(lastParams),
  }
}

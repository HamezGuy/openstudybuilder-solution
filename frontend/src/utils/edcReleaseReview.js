// These native collections share the selected study/version and study:read.
// The design-cell and schedule routes return complete lists. Other routes page.
export const workingResources = [
  { key: 'arms', label: 'Arms', path: 'study-arms' },
  { key: 'cohorts', label: 'Cohorts', path: 'study-cohorts' },
  { key: 'epochs', label: 'Epochs', path: 'study-epochs' },
  { key: 'elements', label: 'Study elements', path: 'study-elements' },
  {
    key: 'design_cells',
    label: 'Design cells',
    path: 'study-design-cells',
    list: true,
  },
  { key: 'visits', label: 'Visits', path: 'study-visits' },
  { key: 'activities', label: 'Activities', path: 'study-activities' },
  {
    key: 'activity_schedules',
    label: 'Activity schedules',
    path: 'study-activity-schedules',
    list: true,
  },
  { key: 'objectives', label: 'Objectives', path: 'study-objectives' },
  { key: 'endpoints', label: 'Endpoints', path: 'study-endpoints' },
  { key: 'criteria', label: 'Eligibility criteria', path: 'study-criteria' },
  {
    key: 'footnotes',
    label: 'Schedule footnotes',
    path: 'study-soa-footnotes',
  },
]

const validCount = (n) => Number.isSafeInteger(n) && n >= 0

export function reviewSnapshot(snapshot, studyUid, version) {
  const requestedVersion =
    version == null || Number(version) === 0 ? null : String(version)
  if (
    snapshot?.schema_version !== 'osb-authority/1.2' ||
    snapshot.mapping_authority !== 'OpenStudyBuilder' ||
    snapshot.study_uid !== studyUid ||
    (snapshot.study_version ?? null) !== requestedVersion ||
    !['enforced', 'shadow', 'legacy'].includes(snapshot.authority_mode) ||
    typeof snapshot.release_eligible !== 'boolean' ||
    !Array.isArray(snapshot.blockers) ||
    snapshot.blockers.some(
      (row) =>
        !row ||
        !['code', 'detail', 'path'].every((key) => typeof row[key] === 'string')
    ) ||
    snapshot.release_eligible !== (snapshot.blockers.length === 0) ||
    !snapshot.counts ||
    typeof snapshot.counts !== 'object' ||
    Array.isArray(snapshot.counts) ||
    !Object.values(snapshot.counts).every(validCount) ||
    !/^[a-f0-9]{64}$/.test(snapshot.content_hash)
  )
    throw new Error('The response did not match the requested study snapshot.')
  return {
    state: snapshot.release_eligible ? 'eligible' : 'held',
    mode: snapshot.authority_mode,
    hash: snapshot.content_hash,
    counts: snapshot.counts,
    blockers: snapshot.blockers,
  }
}

export function readFailure(error) {
  const status = error?.response?.status
  if (status === 401) return { state: 'signed-out' }
  if (status === 403) return { state: 'denied' }
  const body = error?.response?.data
  const message = [body?.message, body?.detail, body?.detail?.message].find(
    (value) => typeof value === 'string'
  )
  const hold =
    status === 422 &&
    message?.match(/^(USDM_[A-Z0-9_]+_REQUIRED)\b\s*:?\s*([\s\S]*)$/)
  if (hold) {
    return { state: 'held', blockers: [{ code: hold[1], detail: hold[2] }] }
  }
  return { state: 'unavailable', status }
}

export function countResource(resource, data, studyUid) {
  const items = resource.list ? data : data?.items
  if (
    !Array.isArray(items) ||
    items.some(
      (item) => !item || (item.study_uid != null && item.study_uid !== studyUid)
    )
  ) {
    throw new Error('Unexpected study resource response.')
  }
  if (resource.list) return items.length
  if (!validCount(data.total) || data.total < items.length)
    throw new Error('Missing study resource total.')
  return data.total
}

export async function loadWorkingCounts(
  api,
  studyUid,
  version,
  review,
  signal,
  update
) {
  if (['denied', 'signed-out'].includes(review.state)) return
  const pending = workingResources.filter(
    (resource) => !validCount(review.counts?.[resource.key])
  )
  // Keep native Neo4j reads bounded. The two unpaged resources are read once.
  async function worker() {
    while (pending.length && !signal.aborted) {
      const resource = pending.shift()
      let value
      try {
        const { data } = await api.getWorkingResource(
          studyUid,
          resource,
          version,
          signal
        )
        value = { count: countResource(resource, data, studyUid) }
      } catch (error) {
        const failure = readFailure(error)
        value = {
          state: ['signed-out', 'denied'].includes(failure.state)
            ? failure.state
            : 'unavailable',
        }
      }
      if (!signal.aborted) update(resource.key, value)
    }
  }
  await Promise.all(Array.from({ length: Math.min(3, pending.length) }, worker))
}

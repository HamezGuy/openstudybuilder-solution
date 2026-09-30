const fixedTimeFields = new Set(['AVISIT1', 'AVISIT1N', 'AVISIT2', 'AVISIT2N'])

export function analysisMetadataValue(item, key) {
  const value = item[key]
  if (value !== null && value !== undefined && value !== '') return value
  return item.TIMING_MODE === 'UNTIMED' && fixedTimeFields.has(key)
    ? 'Not fixed'
    : 'Not configured'
}

export function analysisMetadataRowKey(type, item) {
  return JSON.stringify([
    type,
    item.STUDYID ?? item.STUDYID_FLOWCHART ?? item.STUDYID_OBJ,
    item.SOURCE_SCHEDULE_UID ?? item.SOURCE_VISIT_UID,
    item.SOURCE_ACTIVITY_UID,
    item.AVISITN,
    item.PARAMCD,
    item.PARAMN,
    item.OBJTV,
    item.ENDPNT,
    item.TMFRM,
  ])
}

import repository from './repository'

const resource = 'integrations/edc'

export default {
  getStudyBundle(studyUid, studyVersion) {
    return repository.get(`${resource}/studies/${studyUid}/study-bundle`, {
      params: { specificStudyVersion: studyVersion },
    })
  },
  send(studyUid, dryRun, studyVersion) {
    return repository.post(
      `${resource}/studies/${studyUid}/study-bundle/send`,
      { dry_run: dryRun },
      {
        params:
          studyVersion != null && Number(studyVersion) !== 0
            ? { study_value_version: studyVersion }
            : {},
      }
    )
  },
}

import repository from './repository'

const resource = 'integrations/edc'

export default {
  getAuthoritySnapshot(studyUid, studyVersion, signal) {
    return repository.get(
      `integrations/study-authority/studies/${studyUid}/snapshot`,
      {
        params: { specificStudyVersion: studyVersion ?? 0 },
        ignoreErrors: true,
        signal,
      }
    )
  },
  getWorkingResource(studyUid, resource, studyVersion, signal) {
    return repository.get(`studies/${studyUid}/${resource.path}`, {
      params: {
        specificStudyVersion: studyVersion ?? 0,
        ...(resource.list ? {} : { page_size: 1, total_count: true }),
      },
      ignoreErrors: true,
      signal,
    })
  },
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

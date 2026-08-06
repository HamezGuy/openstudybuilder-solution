import repository from './repository'

const resource = 'integrations/edc'

export default {
  getStudyBundle(studyUid) {
    return repository.get(`${resource}/studies/${studyUid}/study-bundle`)
  },
  send(studyUid, dryRun) {
    return repository.post(`${resource}/studies/${studyUid}/study-bundle/send`, {
      dry_run: dryRun,
    })
  },
}

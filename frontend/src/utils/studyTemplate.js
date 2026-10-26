/**
 * The study uid of the active study template, or '' when there is none.
 *
 * GET studies/template answers null when no template is set and 403 under the
 * strict delegated profile, where the form keeps its initial {}. Only a real
 * uid may offer "Create a new study with default settings": without one the
 * copy would post to studies/undefined/clone/async and end in a 404.
 */
export function studyTemplateUid(template) {
  const uid = template?.study_uid
  return typeof uid === 'string' ? uid.trim() : ''
}

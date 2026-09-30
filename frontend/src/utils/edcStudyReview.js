/** Current V2 study review projection. Never reconstruct a writable definition. */
export function edcStudyReview(bundle) {
  if (
    bundle?.formatVersion !== '2.0' ||
    bundle?.profile?.id !== 'edc-study-exchange/2' ||
    bundle?.profile?.modelVersion !== '4.0.0' ||
    !bundle?.definition?.document?.study ||
    !Array.isArray(bundle?.execution?.visits) ||
    !Array.isArray(bundle?.execution?.forms?.forms) ||
    !Array.isArray(bundle?.execution?.visitFormAssignments)
  ) {
    throw new Error('The response is not a complete USDM 4 study exchange V2.')
  }
  const study = bundle.definition.document.study
  const versions = Array.isArray(study.versions) ? study.versions : []
  const selection = bundle.definition.selection
  const version = versions.find((item) => item.id === selection?.versionId)
  const design = version?.studyDesigns?.find(
    (item) => item.id === selection?.designId
  )
  const report = bundle.extensions?._osbExport
  const entityCounts = new Map()
  const visit = (value) => {
    if (Array.isArray(value)) {
      value.forEach(visit)
    } else if (value && typeof value === 'object') {
      if (typeof value.instanceType === 'string') {
        entityCounts.set(
          value.instanceType,
          (entityCounts.get(value.instanceType) ?? 0) + 1
        )
      }
      Object.values(value).forEach(visit)
    }
  }
  visit(study)
  return {
    name: study.name ?? study.label ?? study.id,
    version: version?.versionIdentifier ?? version?.name ?? version?.id ?? null,
    design: design?.name ?? design?.id ?? null,
    selectionComplete: Boolean(version && design),
    counts: {
      visits: bundle.execution.visits.length,
      forms: bundle.execution.forms.forms.length,
      fields: bundle.execution.forms.forms.reduce(
        (total, form) => total + (form.fields?.length ?? 0),
        0
      ),
      assignments: bundle.execution.visitFormAssignments.length,
      versions: versions.length,
      designs: versions.reduce(
        (total, item) => total + (item.studyDesigns?.length ?? 0),
        0
      ),
    },
    entities: [...entityCounts.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, count]) => ({ name, count })),
    mappingReport: report?.mappingReport ?? null,
    censusRows: report?.census?.rows ?? [],
    authority: report?.mappingAuthority ?? null,
  }
}

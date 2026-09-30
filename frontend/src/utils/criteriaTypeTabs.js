// Tabs route by name, while source and destination terminology retain their IDs.
export function criteriaTypeTabs(terms) {
  const groups = new Map()
  for (const term of terms) {
    const name = term.sponsor_preferred_name
    const existing = groups.get(name)
    if (existing) {
      if (!existing.term_uids.includes(term.term_uid)) {
        existing.term_uids.push(term.term_uid)
      }
      if (
        Number.isFinite(term.order) &&
        (!Number.isFinite(existing.order) || term.order < existing.order)
      ) {
        existing.order = term.order
      }
    } else {
      groups.set(name, { ...term, term_uids: [term.term_uid] })
    }
  }
  return [...groups.values()].sort(
    (a, b) =>
      (Number.isFinite(a.order) ? a.order : Number.MAX_SAFE_INTEGER) -
      (Number.isFinite(b.order) ? b.order : Number.MAX_SAFE_INTEGER)
  )
}

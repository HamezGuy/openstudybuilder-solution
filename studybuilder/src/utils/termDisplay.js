/** Native CT terms and dictionary terms use different name properties. */
export function termDisplayName(term) {
  for (const value of [term?.sponsor_preferred_name, term?.name]) {
    if (typeof value === 'string' && value.trim()) return value
  }
  return term?.term_uid ? `Term unavailable (${term.term_uid})` : 'Not configured'
}

export function hasMetadataValue(value) {
  if (value == null) return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'string') return value.trim().length > 0
  return true
}

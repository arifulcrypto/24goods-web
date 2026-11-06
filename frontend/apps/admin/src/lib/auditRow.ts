import type { AuditRowV5 } from '../api-v5'

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

/** before/after of an audit row: explicit fields, else `detail.before/after`, else the whole detail as "after". */
export function auditSides(r: AuditRowV5): { before: unknown; after: unknown } {
  if (r.before !== undefined || r.after !== undefined) return { before: r.before ?? null, after: r.after ?? null }
  const d = r.detail
  if (isObj(d) && ('before' in d || 'after' in d)) return { before: d.before ?? null, after: d.after ?? null }
  return { before: null, after: d ?? null }
}
/** Date picker value (Date[]) → ISO since/until; an empty/invalid range gives {}. */
export function rangeToQuery(r: [Date, Date] | null): { since?: string; until?: string } {
  if (!r) return {}
  return { since: r[0].toISOString(), until: r[1].toISOString() }
}

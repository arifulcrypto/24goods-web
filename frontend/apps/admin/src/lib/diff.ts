/** Flat before/after JSON diff for the audit drawer. Pure; values are rendered as text only. */
export type DiffKind = 'added' | 'removed' | 'changed' | 'same'
export interface DiffRow {
  path: string
  kind: DiffKind
  before?: unknown
  after?: unknown
}
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

function flatten(v: unknown, prefix: string, out: Map<string, unknown>): void {
  if (isObj(v)) {
    const keys = Object.keys(v)
    if (!keys.length && prefix) out.set(prefix, v)
    for (const k of keys) flatten(v[k], prefix ? `${prefix}.${k}` : k, out)
  } else if (Array.isArray(v)) {
    if (!v.length && prefix) out.set(prefix, v)
    v.forEach((x, i) => flatten(x, `${prefix}[${i}]`, out))
  } else {
    out.set(prefix || '(value)', v)
  }
}
export function jsonDiff(before: unknown, after: unknown, includeSame = false): DiffRow[] {
  const a = new Map<string, unknown>()
  const b = new Map<string, unknown>()
  if (before !== null && before !== undefined) flatten(before, '', a)
  if (after !== null && after !== undefined) flatten(after, '', b)
  const paths = Array.from(new Set([...a.keys(), ...b.keys()])).sort()
  const rows: DiffRow[] = []
  for (const path of paths) {
    const inA = a.has(path)
    const inB = b.has(path)
    if (inA && !inB) rows.push({ path, kind: 'removed', before: a.get(path) })
    else if (!inA && inB) rows.push({ path, kind: 'added', after: b.get(path) })
    else if (JSON.stringify(a.get(path)) !== JSON.stringify(b.get(path))) rows.push({ path, kind: 'changed', before: a.get(path), after: b.get(path) })
    else if (includeSame) rows.push({ path, kind: 'same', before: a.get(path), after: b.get(path) })
  }
  return rows
}
export function showValue(v: unknown): string {
  if (v === undefined) return ''
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

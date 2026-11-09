/**
 * Delivery presentation helpers (review-v3 §5): format presets, separators, card-line split
 * preview, redeem-URL validation and after-sales window presets. Pure functions (unit-tested).
 */

/** Separators the backend accepts (`delivery_separator`). The value is what is stored. */
export const SEPARATORS = [
  { value: '----', key: 'dashes' },
  { value: '|', key: 'pipe' },
  { value: ',', key: 'comma' },
  { value: '\t', key: 'tab' },
  { value: ':', key: 'colon' },
] as const
export type Separator = (typeof SEPARATORS)[number]['value']
export const DEFAULT_SEPARATOR: Separator = '----'

export function isSeparator(v: unknown): v is Separator {
  return SEPARATORS.some((s) => s.value === v)
}

/** Label presets (joined with `----` in the UI; re-joined with the chosen separator on save). */
export const FORMAT_PRESETS = [
  { key: 'card', labels: ['Card code'] },
  { key: 'account', labels: ['Account', 'Password'] },
  { key: 'account2fa', labels: ['Account', 'Password', '2FA', 'Notes'] },
  { key: 'email', labels: ['Email', 'Password', 'Recovery email', '2FA'] },
] as const
export type FormatPresetKey = (typeof FORMAT_PRESETS)[number]['key'] | 'custom'

/** Split a stored/typed `delivery_format` into labels using the separator; trims, drops empties. */
export function formatLabels(format: string | null | undefined, sep: string = DEFAULT_SEPARATOR): string[] {
  if (!format) return []
  return format
    .split(sep)
    .map((s) => s.trim())
    .filter(Boolean)
}

/** Join labels into `delivery_format` with the separator (null when there are none). */
export function joinFormat(labels: readonly string[], sep: string = DEFAULT_SEPARATOR): string | null {
  const clean = labels.map((l) => l.trim()).filter(Boolean)
  return clean.length ? clean.join(sep) : null
}

/** Which preset (if any) a label list corresponds to. */
export function presetFor(labels: readonly string[]): FormatPresetKey {
  if (!labels.length) return 'card'
  const p = FORMAT_PRESETS.find(
    (x) => x.labels.length === labels.length && x.labels.every((l, i) => l.toLowerCase() === labels[i]!.toLowerCase()),
  )
  return p ? p.key : 'custom'
}

export interface CardField {
  label: string
  value: string
  /** true when the label is a generated "Field N" (more values than labels). */
  extra: boolean
}

/**
 * Split one card line into labelled fields, mirroring the buyer view (review-v3 §5):
 * - no format (or a single label) → the whole line is one block;
 * - extra values beyond the labels are labelled `Field N` (1-based position);
 * - a per-line `|exp=YYYY-MM-DD` import suffix is not part of the card and is stripped.
 * `fieldLabel(n)` produces the "Field N" label (i18n in the UI).
 */
export function splitCard(
  line: string,
  labels: readonly string[],
  sep: string = DEFAULT_SEPARATOR,
  fieldLabel: (n: number) => string = (n) => `Field ${n}`,
): CardField[] {
  const raw = stripExpiry(line).trim()
  if (!raw) return []
  if (labels.length <= 1) return [{ label: labels[0] ?? fieldLabel(1), value: raw, extra: labels.length === 0 }]
  const parts = sep ? raw.split(sep) : [raw]
  return parts.map((value, i) => ({
    label: i < labels.length ? labels[i]! : fieldLabel(i + 1),
    value: value.trim(),
    extra: i >= labels.length,
  }))
}

const EXP_RE = /\|exp=(\d{4}-\d{2}-\d{2})\s*$/

/** Remove the import-only `|exp=YYYY-MM-DD` suffix. */
export function stripExpiry(line: string): string {
  return line.replace(EXP_RE, '')
}
/** The per-line expiry, if present and a real date. */
export function lineExpiry(line: string): string | null {
  const m = EXP_RE.exec(line)
  if (!m) return null
  const d = new Date(`${m[1]}T00:00:00Z`)
  return Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== m[1] ? null : m[1]!
}

/** `redeem_url` must be an absolute https URL with a host and no credentials. Empty is allowed. */
export function isValidRedeemUrl(raw: string): boolean {
  const s = raw.trim()
  if (!s) return true
  if (s.length > 500) return false
  try {
    const u = new URL(s)
    return u.protocol === 'https:' && !!u.hostname && !u.username && !u.password
  } catch {
    return false
  }
}

/** After-sales window presets → `after_sales_hours` (null = site default; 0 = none). */
export const AFTER_SALES_PRESETS = [
  { key: 'default', hours: null },
  { key: 'h24', hours: 24 },
  { key: 'd3', hours: 72 },
  { key: 'd7', hours: 168 },
  { key: 'd28', hours: 672 },
  { key: 'none', hours: 0 },
] as const
export type AfterSalesPresetKey = (typeof AFTER_SALES_PRESETS)[number]['key'] | 'custom'
export const SITE_DEFAULT_AFTER_SALES_HOURS = 672

export function afterSalesPresetFor(hours: number | null | undefined): AfterSalesPresetKey {
  const h = hours ?? null
  return AFTER_SALES_PRESETS.find((p) => p.hours === h)?.key ?? 'custom'
}

/** v7 after-sales control: site default | hours | days | months. */
export type AfterSalesUnit = 'default' | 'hours' | 'days' | 'months'
export const AFTER_SALES_UNITS: AfterSalesUnit[] = ['default', 'hours', 'days', 'months']

export function afterSalesToForm(
  hours: number | null | undefined,
  months: number | null | undefined,
): { unit: AfterSalesUnit; value: number } {
  if (months != null) return { unit: 'months', value: months }
  if (hours == null) return { unit: 'default', value: 1 }
  if (hours > 0 && hours % 24 === 0) return { unit: 'days', value: hours / 24 }
  return { unit: 'hours', value: hours }
}

/** Setting one unit clears the other (the server enforces the same rule). */
export function afterSalesFromForm(
  unit: AfterSalesUnit,
  value: number,
): { after_sales_hours: number | null; after_sales_months: number | null } {
  if (unit === 'hours') return { after_sales_hours: value, after_sales_months: null }
  if (unit === 'days') return { after_sales_hours: value * 24, after_sales_months: null }
  if (unit === 'months') return { after_sales_hours: null, after_sales_months: value }
  return { after_sales_hours: null, after_sales_months: null }
}

export function afterSalesValid(unit: AfterSalesUnit, value: number): boolean {
  if (!Number.isInteger(value)) return unit === 'default'
  if (unit === 'hours') return value >= 0 && value <= 8760
  if (unit === 'days') return value >= 1 && value <= 365
  if (unit === 'months') return value >= 1 && value <= 12
  return true
}

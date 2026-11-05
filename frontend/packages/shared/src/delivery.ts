import type { DeliveryInfo } from './types'

export interface CardField {
  label: string
  value: string
  /** true when the label was synthesised ("Field N") — callers localise it. */
  extra: boolean
  index: number
}

/**
 * Split one card by the delivery separator into labelled fields. Labels come from
 * `format_labels`; surplus fields get `extra: true` (rendered as "Field N"). With no format
 * (or no separator hit) the code is a single unlabelled block.
 */
export function splitCard(code: string, info: Pick<DeliveryInfo, 'format_labels' | 'separator'> | null | undefined): CardField[] {
  const labels = info?.format_labels ?? []
  const sep = info?.separator ?? ''
  if (!sep || labels.length === 0) return [{ label: '', value: code, extra: false, index: 0 }]
  const parts = code.split(sep)
  if (parts.length === 1 && labels.length <= 1) return [{ label: labels[0] ?? '', value: code, extra: false, index: 0 }]
  return parts.map((value, index) => ({
    label: labels[index] ?? '',
    value,
    extra: index >= labels.length,
    index,
  }))
}

export function cardsToText(codes: readonly string[]): string {
  return codes.join('\n')
}

/** Whole days left until an ISO deadline; null when no deadline, 0 when past. */
export function daysLeft(untilIso: string | null | undefined, now: number = Date.now()): number | null {
  if (!untilIso) return null
  const t = Date.parse(untilIso)
  if (Number.isNaN(t)) return null
  return Math.max(0, Math.ceil((t - now) / 86_400_000))
}

export function isExpired(untilIso: string | null | undefined, now: number = Date.now()): boolean {
  if (!untilIso) return true
  const t = Date.parse(untilIso)
  return Number.isNaN(t) || t <= now
}

/** Trigger a client-side .txt download (object URL, revoked right after). */
export function downloadText(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** After-sales badge: ≤48 h shown in hours, longer in days; 0 / null → no badge. */
export function afterSalesBadge(hours: number | null | undefined): { n: number; unit: 'h' | 'd' } | null {
  if (!hours || hours <= 0) return null
  return hours <= 48 ? { n: hours, unit: 'h' } : { n: Math.round(hours / 24), unit: 'd' }
}

/** Delivered/completed, inside the window, and no open ticket. */
export function canRequestAfterSales(
  o: { status: string; after_sales_until?: string | null; after_sales?: { status: string } | null },
  now: number = Date.now(),
): boolean {
  if (o.status !== 'delivered' && o.status !== 'completed') return false
  if (isExpired(o.after_sales_until, now)) return false
  return !o.after_sales || !['open', 'processing'].includes(o.after_sales.status)
}

/** Labels of a `delivery_format` string, e.g. "Account----Password" → ["Account", "Password"]. */
export function formatLabels(format: string | null | undefined, separator: string | null | undefined): string[] {
  if (!format) return []
  return format.split(separator || '----').map((x) => x.trim()).filter(Boolean)
}

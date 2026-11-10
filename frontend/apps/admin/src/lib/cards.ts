/** Card management helpers (review-v3 §8). Pure, unit-tested. */
import type { Query } from '@24goods/shared'

export const CARD_STATUSES = ['available', 'reserved', 'sold', 'void'] as const
export type CardStatusV3 = (typeof CARD_STATUSES)[number]

export interface CardFilters {
  status: CardStatusV3 | ''
  batch_id: string
  /** 0/null = no expiry filter. */
  expiring_days: number | null
  order_no: string
  /** v11 W1: only this variant's cards (empty = all). */
  variant_id?: string
}

export const emptyCardFilters = (): CardFilters => ({ status: '', batch_id: '', expiring_days: null, order_no: '', variant_id: '' })

const ORDER_NO_RE = /^[A-Za-z0-9-]{1,40}$/

/**
 * Query for GET /admin/products/{id}/cards. Drops empty values (buildQuery also skips them),
 * clamps expiring_days to 1..365 and ignores malformed order numbers instead of sending them.
 */
export function buildCardQuery(f: CardFilters, page: number, pageSize: number): Query {
  const q: Query = { page, page_size: pageSize }
  if (f.status) q.status = f.status
  if (f.batch_id) q.batch_id = f.batch_id
  if (f.expiring_days && f.expiring_days > 0) q.expiring_days = Math.min(365, Math.max(1, Math.floor(f.expiring_days)))
  if (f.variant_id) q.variant_id = f.variant_id
  const no = f.order_no.trim()
  if (no && ORDER_NO_RE.test(no)) q.order_no = no
  return q
}

export const FIND_MAX_LINES = 100
export const IMPORT_MAX_LINES = 5000

/** Non-empty trimmed lines, de-duplicated (order kept). */
export function cleanLines(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of text.split(/\r?\n/)) {
    const l = raw.trim()
    if (!l || seen.has(l)) continue
    seen.add(l)
    out.push(l)
  }
  return out
}

/** Count of non-empty lines (for the import counter; duplicates are reported by the server). */
export function countLines(text: string): number {
  let n = 0
  for (const l of text.split(/\r?\n/)) if (l.trim()) n++
  return n
}

/** Voiding is only allowed from available/reserved (DB trigger enforces it too). */
export function canVoid(status: string): boolean {
  return status === 'available' || status === 'reserved'
}

/** Days until `iso` (ceil), or null. Negative when already expired. */
export function daysUntil(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return null
  return Math.ceil((t - now) / 86_400_000)
}

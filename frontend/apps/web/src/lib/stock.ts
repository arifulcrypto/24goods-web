import type { ProductSummary } from '@24goods/shared'

/** Per-order cap the storefront offers (the server enforces its own limits as well). */
export const MAX_PER_ORDER = 10

type Stocked = Pick<ProductSummary, 'stock'>

/**
 * One reading of `stock` for every view (issue 49 C), manual and automatic alike:
 * `null` = unlimited, `0` = unavailable, a positive number caps the quantity.
 */
export function isSoldOut(p: Stocked): boolean {
  return p.stock !== null && p.stock !== undefined && p.stock <= 0
}

/** Highest quantity the buyer may pick: stock-capped, never below 1 so the stepper stays valid. */
export function maxOrderQty(p: Stocked, cap = MAX_PER_ORDER): number {
  if (p.stock === null || p.stock === undefined) return cap
  return Math.max(1, Math.min(cap, p.stock))
}

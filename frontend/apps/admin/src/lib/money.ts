/** Money / rate conversions for the v5 admin forms. All server values are integer minor units / permille. */

/** Yuan text ("19.9", "19.90") → minor units, or null if not a valid non-negative amount (≤ 2 dp). */
export function yuanToMinor(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined) return null
  const s = String(v).trim()
  if (!/^\d{1,9}(\.\d{1,2})?$/.test(s)) return null
  const [i, f = ''] = s.split('.')
  return Number(i) * 100 + Number((f + '00').slice(0, 2))
}
export function minorToYuan(m: number | null | undefined): string {
  if (m === null || m === undefined || !Number.isFinite(m)) return ''
  const sign = m < 0 ? '-' : ''
  const a = Math.abs(Math.trunc(m))
  return `${sign}${Math.floor(a / 100)}.${String(a % 100).padStart(2, '0')}`
}
/** Percent (as shown, e.g. 12.5) → permille (125). */
export const pctToPermille = (pct: number): number => Math.round(pct * 10)
export const permilleToPct = (pm: number): number => Math.round(pm) / 10

/** Price rounded UP to a multiple of `roundTo` minor units (1 = no rounding). */
export function roundUp(minor: number, roundTo: number): number {
  const r = Number.isInteger(roundTo) && roundTo > 1 ? roundTo : 1
  return Math.ceil(minor / r) * r
}
/**
 * Suggested sell price from a cost: percent markup is in permille (200 = 20 %), fixed in minor units.
 * Mirrors the server's `roundup(cost + markup, round_to_minor)`; the server value is authoritative.
 */
export function suggestPrice(costMinor: number, kind: 'percent' | 'fixed', value: number, roundTo: number): number {
  const markup = kind === 'percent' ? Math.ceil((costMinor * value) / 1000) : value
  return roundUp(costMinor + markup, roundTo)
}
/** price − cost in minor units; null when either is unknown. */
export function marginMinor(priceMinor: number | null, costMinor: number | null | undefined): number | null {
  if (priceMinor === null || costMinor === null || costMinor === undefined) return null
  return priceMinor - costMinor
}
export function marginPct(priceMinor: number | null, costMinor: number | null | undefined): number | null {
  const m = marginMinor(priceMinor, costMinor)
  if (m === null || !priceMinor) return null
  return Math.round((m / priceMinor) * 1000) / 10
}

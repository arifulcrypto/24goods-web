/**
 * v5 pure helpers (review-v5): promo/fee maths, storefront persistence, Turnstile gating,
 * reseller markup preview, manual-delivery note. No DOM/Vue here so everything is unit-tested.
 */
import { fromMinor, toMinor } from './format'
import type { MarkupKind, SiteInfo } from './types'

// ---------- promo + fee lines (§6) ----------

export interface FeeBreakdown {
  subtotal: string
  discount: string
  fee: string
  total: string
}

/**
 * Display-only: fee = ceil((subtotal - discount) * permille / 1000); total = subtotal - discount + fee.
 * The discount is clamped to `< subtotal` like the server (amount stays > 0). The server owns the numbers.
 */
export function feeBreakdown(subtotal: string, discountMinor: number | null | undefined, permille: number): FeeBreakdown {
  const s = toMinor(subtotal) ?? 0n
  let d = BigInt(Math.max(0, Math.floor(discountMinor ?? 0)))
  if (d >= s) d = s > 0n ? s - 1n : 0n
  const base = s - d
  const pm = Number.isInteger(permille) && permille > 0 ? BigInt(permille) : 0n
  const fee = (base * pm + 999n) / 1000n
  return { subtotal: fromMinor(s), discount: fromMinor(d), fee: fromMinor(fee), total: fromMinor(base + fee) }
}

/**
 * Largest-remainder split of `total` over lines (weights = line subtotals, caps = subtotal - 1),
 * a port of the server's promo `allocate` so a cart preview matches create_checkout.
 */
export function allocateDiscount(total: bigint, subs: bigint[]): bigint[] {
  const n = subs.length
  const caps = subs.map((s) => (s > 0n ? s - 1n : 0n))
  const shares: bigint[] = subs.map(() => 0n)
  let remaining = total
  let active = subs.map((_, i) => i).filter((i) => caps[i]! > 0n)
  while (remaining > 0n && active.length) {
    const w = active.reduce((a, i) => a + subs[i]!, 0n)
    const floors = new Map<number, bigint>()
    const rems = new Map<number, bigint>()
    for (const i of active) {
      floors.set(i, (remaining * subs[i]!) / w)
      rems.set(i, (remaining * subs[i]!) % w)
    }
    let leftover = remaining - [...floors.values()].reduce((a, b) => a + b, 0n)
    const order = [...active].sort((a, b) => (rems.get(b)! > rems.get(a)! ? 1 : rems.get(b)! < rems.get(a)! ? -1 : a - b))
    for (const i of order) {
      if (leftover <= 0n) break
      floors.set(i, floors.get(i)! + 1n)
      leftover -= 1n
    }
    let overflow = 0n
    for (const i of active) {
      shares[i] = shares[i]! + floors.get(i)!
      if (shares[i]! > caps[i]!) {
        overflow += shares[i]! - caps[i]!
        shares[i] = caps[i]!
      }
    }
    remaining = overflow
    active = active.filter((i) => shares[i]! < caps[i]!)
  }
  return n ? shares : []
}

/**
 * Display-only cart fee with the SERVER's rule (checkout.py): the fee is ceiled PER LINE on
 * (line subtotal - line discount), then summed. Never `ceil(sum * pm)` (issue 23 bug A:
 * 5 x 1.01 @3% is 0.20, not 0.16). `lineDiscounts` defaults to a proportional split of
 * `discountMinor`.
 */
export function feeBreakdownLines(
  lineSubtotals: readonly string[],
  permille: number,
  discountMinor = 0,
  lineDiscounts?: readonly number[],
): FeeBreakdown {
  const subs = lineSubtotals.map((x) => toMinor(x) ?? 0n)
  const total = BigInt(Math.max(0, Math.floor(discountMinor)))
  const discs =
    lineDiscounts && lineDiscounts.length === subs.length
      ? lineDiscounts.map((d) => BigInt(Math.max(0, Math.floor(d))))
      : allocateDiscount(total, subs)
  const pm = Number.isInteger(permille) && permille > 0 ? BigInt(permille) : 0n
  let sub = 0n
  let disc = 0n
  let fee = 0n
  subs.forEach((s, i) => {
    let d = discs[i] ?? 0n
    if (d >= s) d = s > 0n ? s - 1n : 0n
    sub += s
    disc += d
    fee += ((s - d) * pm + 999n) / 1000n
  })
  return { subtotal: fromMinor(sub), discount: fromMinor(disc), fee: fromMinor(fee), total: fromMinor(sub - disc + fee) }
}

/** i18n key of a promo error code (anything unknown → promo_invalid). */
export function promoErrorKey(code: string | null | undefined): string {
  const known = [
    'promo_invalid',
    'promo_not_started',
    'promo_expired',
    'promo_exhausted',
    'promo_user_limit',
    'promo_min_subtotal',
    'promo_not_applicable',
    'feature_disabled',
    'turnstile_failed',
  ]
  return known.includes(code ?? '') ? `errors.${code}` : 'errors.promo_invalid'
}

/** Promo input → canonical upper-case code, or null when it cannot be a code. */
export function cleanPromoCode(raw: string): string | null {
  const v = raw.trim().toUpperCase()
  return /^[A-Z0-9_-]{2,32}$/.test(v) ? v : null
}

/** The promo UI is hidden when the feature is off or a reseller storefront is active (§6, §7). */
export function promoAvailable(site: SiteInfo | null): boolean {
  if (!site) return false
  if (site.storefront) return false
  return site.features?.promo_codes !== false
}

// ---------- storefront (§7) ----------

export const STORE_KEY = 'kesiv.store'
export const STOREFRONT_HEADER = 'X-Storefront'
type KV = Pick<Storage, 'getItem' | 'setItem'> & Partial<Pick<Storage, 'removeItem'>>
export const STORE_TTL_MS = 30 * 24 * 3600 * 1000

export function cleanStoreSlug(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const v = raw.trim().toLowerCase()
  return /^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$/.test(v) ? v : null
}
function store(storage?: KV): KV {
  return storage ?? globalThis.localStorage
}
export function rememberStore(slug: string | null, storage?: KV, now = Date.now()): void {
  try {
    if (slug) store(storage).setItem(STORE_KEY, JSON.stringify({ slug, exp: now + STORE_TTL_MS }))
  } catch {
    /* ignore */
  }
}
export function forgetStore(storage?: KV): void {
  try {
    store(storage).removeItem?.(STORE_KEY)
  } catch {
    /* ignore */
  }
}
export function recalledStore(storage?: KV, now = Date.now()): string | null {
  try {
    const raw = store(storage).getItem(STORE_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as { slug?: unknown; exp?: unknown }
    if (typeof v.exp !== 'number' || v.exp < now) {
      forgetStore(storage)
      return null
    }
    return cleanStoreSlug(v.slug)
  } catch {
    return null
  }
}
/** `?store=slug` remembers (30 days), `?store=off` forgets; returns the active slug. */
export function captureStore(search: string, storage?: KV, now = Date.now()): string | null {
  const raw = new URLSearchParams(search).get('store')
  if (raw?.trim().toLowerCase() === 'off') {
    forgetStore(storage)
    return null
  }
  const fromUrl = cleanStoreSlug(raw)
  if (fromUrl) rememberStore(fromUrl, storage, now)
  return fromUrl ?? recalledStore(storage, now)
}
/** Headers added to every API request. */
export function storefrontHeaders(storage?: KV): Record<string, string> {
  const s = recalledStore(storage)
  return s ? { [STOREFRONT_HEADER]: s } : {}
}

// ---------- Turnstile (§5.1) ----------

export type TurnstileAction =
  | 'login'
  | 'register'
  | 'instant_account'
  | 'password_reset'
  | 'lookup'
  | 'promo_validate'
  | 'reseller_apply'

/** The widget is needed only when enabled, a site key exists and the action is protected. */
export function turnstileNeeded(site: SiteInfo | null | undefined, action: TurnstileAction): boolean {
  const t = site?.turnstile
  return !!t && t.enabled === true && !!t.site_key && t.actions.includes(action)
}

// ---------- reseller markup (§7) ----------

/** Markup in minor units: percent → ceil(base × permille / 1000); fixed → value. */
export function markupAmount(baseMinor: number, kind: MarkupKind, value: number): number {
  const b = BigInt(Math.max(0, Math.floor(baseMinor)))
  const v = BigInt(Math.max(0, Math.floor(value)))
  return Number(kind === 'percent' ? (b * v + 999n) / 1000n : v)
}
export function markedUpPrice(baseMinor: number, kind: MarkupKind, value: number): number {
  return baseMinor + markupAmount(baseMinor, kind, value)
}
/** "12.5" (percent) → 125 permille; null when invalid. */
export function percentToPermille(input: string): number | null {
  const m = /^\s*(\d{1,3})(?:\.(\d))?\s*%?\s*$/.exec(input)
  if (!m) return null
  return Number(m[1]) * 10 + Number(m[2] ?? 0)
}
export function permilleToPercent(pm: number): string {
  return pm % 10 === 0 ? String(pm / 10) : (pm / 10).toFixed(1)
}
/** Yuan/dollar input "3.5" → minor 350; null when invalid. */
export function amountToMinor(input: string): number | null {
  const m = toMinor(input.trim())
  return m === null || m < 0n ? null : Number(m)
}

/** Slug `{slug}.{base}` preview; the slug is shown as typed once it is syntactically valid. */
export function storeHostPreview(slug: string, baseDomain: string): string {
  const s = cleanStoreSlug(slug)
  return `${s ?? (slug.trim().toLowerCase() || 'your-store')}.${baseDomain}`
}

// ---------- manual delivery promise (§11) ----------

/** Substitute `{hours}` in the server-provided note. */
export function manualNote(note: string | null | undefined, hours: number): string {
  return (note ?? '').replace(/\{hours\}/g, String(hours))
}
export function manualHours(eta: number | null | undefined, siteDefault: number | null | undefined): number {
  return eta && eta > 0 ? eta : siteDefault && siteDefault > 0 ? siteDefault : 24
}

// ---------- white-label storefronts ----------

/** A storefront hides the platform brand everywhere (default on; only `white_label: false` opts out). */
export function isWhiteLabel(site: SiteInfo | null | undefined): boolean {
  const sf = site?.storefront
  return !!sf && sf.white_label !== false
}
/** First grapheme of a store name for the monogram fallback. */
export function storeInitial(name: string | null | undefined): string {
  return Array.from((name ?? '').trim())[0]?.toUpperCase() ?? '?'
}

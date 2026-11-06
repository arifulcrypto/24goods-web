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
type KV = Pick<Storage, 'getItem' | 'setItem'>

export function cleanStoreSlug(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const v = raw.trim().toLowerCase()
  return /^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$/.test(v) ? v : null
}
export function rememberStore(slug: string | null, storage?: KV): void {
  try {
    const st = storage ?? globalThis.sessionStorage
    if (slug) st.setItem(STORE_KEY, slug)
  } catch {
    /* ignore */
  }
}
export function recalledStore(storage?: KV): string | null {
  try {
    return cleanStoreSlug((storage ?? globalThis.sessionStorage).getItem(STORE_KEY))
  } catch {
    return null
  }
}
/** Capture `?store=slug` from a location search string (persisting it) and return the active slug. */
export function captureStore(search: string, storage?: KV): string | null {
  const fromUrl = cleanStoreSlug(new URLSearchParams(search).get('store'))
  if (fromUrl) rememberStore(fromUrl, storage)
  return fromUrl ?? recalledStore(storage)
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

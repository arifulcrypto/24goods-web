import type { AdminPromo, AdminPromoInput } from '../api-v5'
import { minorToYuan, pctToPermille, permilleToPct, yuanToMinor } from './money'

/** Crockford base32 without I L O U (matches the server's generator alphabet). */
const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
export function randomCode(len = 8, rnd: (n: number) => Uint8Array = (n) => crypto.getRandomValues(new Uint8Array(n))): string {
  const bytes = rnd(len)
  let out = ''
  for (let i = 0; i < len; i++) out += ALPHABET[bytes[i]! % 32]
  return out
}

export interface PromoForm {
  code: string
  kind: 'percent' | 'fixed'
  /** percent as shown (12.5) or yuan text ("5.00") */
  value: string
  min_subtotal: string
  max_discount: string
  per_user_limit: number
  usage_limit: number | null
  window: [string, string] | null
  product_ids: string[]
  category_slugs: string[]
  note: string
  enabled: boolean
}
export const emptyPromoForm = (): PromoForm => ({
  code: '', kind: 'percent', value: '10', min_subtotal: '', max_discount: '', per_user_limit: 1, usage_limit: null,
  window: null, product_ids: [], category_slugs: [], note: '', enabled: true,
})
export function formFromPromo(p: AdminPromo): PromoForm {
  return {
    code: p.code,
    kind: p.kind,
    value: p.kind === 'percent' ? String(permilleToPct(p.value)) : minorToYuan(p.value),
    min_subtotal: p.min_subtotal_minor ? minorToYuan(p.min_subtotal_minor) : '',
    max_discount: p.max_discount_minor != null ? minorToYuan(p.max_discount_minor) : '',
    per_user_limit: p.per_user_limit,
    usage_limit: p.usage_limit,
    window: p.starts_at && p.ends_at ? [p.starts_at, p.ends_at] : null,
    product_ids: [...p.product_ids],
    category_slugs: [...p.category_slugs],
    note: p.note ?? '',
    enabled: p.enabled,
  }
}
export type PromoError = 'code' | 'value' | 'min' | 'max' | 'limit'
/** Form → API body (percent → permille, yuan → minor). Returns the first validation error instead if invalid. */
export function promoToInput(f: PromoForm): { ok: AdminPromoInput } | { err: PromoError } {
  const code = f.code.trim().toUpperCase()
  if (!/^[A-Z0-9_-]{3,32}$/.test(code)) return { err: 'code' }
  let value: number
  if (f.kind === 'percent') {
    const n = Number(f.value)
    if (!Number.isFinite(n) || n <= 0 || n >= 100) return { err: 'value' }
    value = pctToPermille(n)
    if (value < 1 || value > 999) return { err: 'value' }
  } else {
    const m = yuanToMinor(f.value)
    if (m === null || m <= 0) return { err: 'value' }
    value = m
  }
  const min = f.min_subtotal.trim() ? yuanToMinor(f.min_subtotal) : 0
  if (min === null) return { err: 'min' }
  const max = f.max_discount.trim() ? yuanToMinor(f.max_discount) : null
  if (f.max_discount.trim() && (max === null || max <= 0)) return { err: 'max' }
  if (!Number.isInteger(f.per_user_limit) || f.per_user_limit < 1) return { err: 'limit' }
  if (f.usage_limit !== null && (!Number.isInteger(f.usage_limit) || f.usage_limit < 1)) return { err: 'limit' }
  return {
    ok: {
      code, kind: f.kind, value, min_subtotal_minor: min, max_discount_minor: max,
      starts_at: f.window?.[0] ?? null, ends_at: f.window?.[1] ?? null,
      usage_limit: f.usage_limit, per_user_limit: f.per_user_limit,
      product_ids: f.product_ids, category_slugs: f.category_slugs,
      enabled: f.enabled, note: f.note.trim() || null,
    },
  }
}
/** "10%" / "¥5.00" */
export function promoValueText(p: Pick<AdminPromo, 'kind' | 'value'>): string {
  return p.kind === 'percent' ? `${permilleToPct(p.value)}%` : `¥${minorToYuan(p.value)}`
}

import { describe, expect, it } from 'vitest'
import {
  ApiClient,
  captureStore,
  cleanPromoCode,
  cleanStoreSlug,
  recalledStore,
  STORE_TTL_MS,
  feeBreakdown,
  isWhiteLabel,
  manualNote,
  markedUpPrice,
  markupAmount,
  percentToPermille,
  promoAvailable,
  promoErrorKey,
  storefrontHeaders,
  turnstileNeeded,
  type SiteInfo,
} from '../src'

const mem = () => {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
}
const site = (o: Partial<SiteInfo>): SiteInfo => ({ brand: 'x', announcement: null, after_sales_default_hours: 0, support_hours: null, pages: [], ...o })

describe('fee lines with discount', () => {
  it('fee is computed on subtotal - discount (ceil)', () => {
    expect(feeBreakdown('100.00', 1000, 30)).toEqual({ subtotal: '100.00', discount: '10.00', fee: '2.70', total: '92.70' })
    expect(feeBreakdown('10.01', 0, 30)).toMatchObject({ fee: '0.31', total: '10.32' })
  })
  it('discount can never zero the order', () => {
    expect(feeBreakdown('5.00', 99999, 0)).toMatchObject({ discount: '4.99', total: '0.01' })
  })
})

describe('promo', () => {
  it('maps error codes to i18n keys', () => {
    expect(promoErrorKey('promo_expired')).toBe('errors.promo_expired')
    expect(promoErrorKey('whatever')).toBe('errors.promo_invalid')
  })
  it('normalizes input', () => {
    expect(cleanPromoCode(' abc123 ')).toBe('ABC123')
    expect(cleanPromoCode('a b')).toBeNull()
  })
  it('hidden when feature off or storefront active', () => {
    expect(promoAvailable(site({}))).toBe(true)
    expect(promoAvailable(site({ features: { promo_codes: false } }))).toBe(false)
    expect(promoAvailable(site({ storefront: { slug: 'a1', store_name: 'A', logo_url: null, welcome: null } }))).toBe(false)
  })
})

describe('storefront', () => {
  it('persists ?store and sends X-Storefront', () => {
    const st = mem()
    expect(captureStore('?store=My-Shop', st)).toBe('my-shop')
    expect(captureStore('', st)).toBe('my-shop')
    expect(storefrontHeaders(st)).toEqual({ 'X-Storefront': 'my-shop' })
    expect(cleanStoreSlug('bad slug!')).toBeNull()
    expect(storefrontHeaders(mem())).toEqual({})
  })
  it('expires after 30 days; ?store=off clears; bad JSON ignored', () => {
    const st = mem()
    const t0 = 1_000_000
    captureStore('?store=shop1', st, t0)
    expect(recalledStore(st, t0 + STORE_TTL_MS - 1)).toBe('shop1')
    expect(recalledStore(st, t0 + STORE_TTL_MS + 1)).toBeNull()
    captureStore('?store=shop1', st, t0)
    expect(captureStore('?store=off', st, t0)).toBeNull()
    expect(recalledStore(st, t0)).toBeNull()
    st.setItem('kesiv.store', 'not-json')
    expect(recalledStore(st, t0)).toBeNull()
  })
  it('client attaches getHeaders to every request', async () => {
    let seen: Headers | Record<string, string> = {}
    const c = new ApiClient({
      getHeaders: () => ({ 'X-Storefront': 'shop1' }),
      fetchImpl: ((_u: string, init: RequestInit) => {
        seen = init.headers as Record<string, string>
        return Promise.resolve(new Response('{}', { status: 200 }))
      }) as unknown as typeof fetch,
    })
    await c.get('/site')
    expect((seen as Record<string, string>)['X-Storefront']).toBe('shop1')
  })
  it('white label unless explicitly disabled', () => {
    const sf = { slug: 'a1', store_name: 'A', logo_url: null, welcome: null }
    expect(isWhiteLabel(site({}))).toBe(false)
    expect(isWhiteLabel(site({ storefront: sf }))).toBe(true)
    expect(isWhiteLabel(site({ storefront: { ...sf, white_label: false } }))).toBe(false)
  })
})

describe('markup preview', () => {
  it('percent rounds up (permille), fixed adds', () => {
    expect(markupAmount(999, 'percent', 100)).toBe(100) // 99.9 -> 100
    expect(markedUpPrice(1000, 'percent', 125)).toBe(1125)
    expect(markedUpPrice(1000, 'fixed', 250)).toBe(1250)
    expect(percentToPermille('12.5')).toBe(125)
    expect(percentToPermille('abc')).toBeNull()
  })
})

describe('turnstile gating', () => {
  const ts = { enabled: true, site_key: 'k', actions: ['login'] }
  it('only when enabled, keyed and action protected', () => {
    expect(turnstileNeeded(site({ turnstile: ts }), 'login')).toBe(true)
    expect(turnstileNeeded(site({ turnstile: ts }), 'register')).toBe(false)
    expect(turnstileNeeded(site({ turnstile: { ...ts, enabled: false } }), 'login')).toBe(false)
    expect(turnstileNeeded(site({ turnstile: { ...ts, site_key: '' } }), 'login')).toBe(false)
    expect(turnstileNeeded(null, 'login')).toBe(false)
  })
})

describe('manual note', () => {
  it('substitutes hours', () => {
    expect(manualNote('Ships in {hours} h, {hours}!', 24)).toBe('Ships in 24 h, 24!')
  })
})

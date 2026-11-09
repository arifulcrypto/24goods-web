import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
vi.mock('../src/api', () => ({ api: {} }))
// node test environment: a minimal in-memory Storage for the persistence assertions
const mem = new Map<string, string>()
vi.stubGlobal('localStorage', {
  getItem: (k: string) => mem.get(k) ?? null,
  setItem: (k: string, v: string) => void mem.set(k, String(v)),
  removeItem: (k: string) => void mem.delete(k),
  clear: () => mem.clear(),
})
const { useCartStore } = await import('../src/stores/cart')

describe('cart totals use the server fee rule (issue 23 A)', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('5 lines of 1.01 @3%: total 5.25; USDT (0%) 5.05; switching permille updates at once', () => {
    const cart = useCartStore()
    const ids = ['a', 'b', 'c', 'd', 'e']
    for (const id of ids) cart.add(id, 1)
    cart.quote = {
      lines: ids.map((id) => ({
        product_id: id, title: id, cover: null, unit_price: '1.01', currency: 'CNY', stock: null,
        available: true, max_qty: 10, needs_input: false, qty: 1, line_subtotal: '1.01',
      })),
      subtotal: '5.05', fee: '0.20', fee_rate: '3%', total: '5.25', currency: 'CNY',
    }
    expect(cart.totals(30)).toMatchObject({ subtotal: '5.05', fee: '0.20', total: '5.25' })
    expect(cart.totals(0)).toMatchObject({ fee: '0.00', total: '5.05' })
  })
})

describe('buyer information never reaches storage (issue 49 A)', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('persists ids/qty/ticks only and keeps the input in memory', () => {
    const cart = useCartStore()
    cart.add('p1', 2, 'FAKE-SESSION-TOKEN')
    cart.setInput('p1', 'FAKE-SESSION-TOKEN-2')
    const raw = localStorage.getItem('kesiv.cart.v1') ?? ''
    expect(raw).not.toContain('FAKE-SESSION')
    expect(JSON.parse(raw)).toEqual([{ product_id: 'p1', qty: 2, selected: true }])
    expect(cart.lines[0]?.buyer_input).toBe('FAKE-SESSION-TOKEN-2')
  })

  it('purges buyer_input written by older builds on load', () => {
    localStorage.setItem('kesiv.cart.v1', JSON.stringify([{ product_id: 'p1', qty: 1, selected: true, buyer_input: 'LEGACY-TOKEN' }]))
    const cart = useCartStore()
    expect(cart.lines[0]?.buyer_input).toBeUndefined()
    expect(localStorage.getItem('kesiv.cart.v1')).not.toContain('LEGACY-TOKEN')
  })

  it('drops inputs on sign-out or account switch, keeps them guest -> signed in', async () => {
    const { useAuthStore } = await import('../src/stores/auth')
    const { nextTick } = await import('vue')
    const auth = useAuthStore()
    const cart = useCartStore()
    cart.add('p1', 1, 'typed-as-guest')
    auth.setUser({ id: 'u1' } as never)
    await nextTick()
    expect(cart.lines[0]?.buyer_input).toBe('typed-as-guest') // instant account mid-checkout
    auth.setUser({ id: 'u2' } as never)
    await nextTick()
    expect(cart.lines[0]?.buyer_input).toBeUndefined()
    cart.setInput('p1', 'typed-as-u2')
    auth.clear()
    await nextTick()
    expect(cart.lines[0]?.buyer_input).toBeUndefined()
  })
})

describe('one stock rule for every view (issue 49 C)', async () => {
  const { isSoldOut, maxOrderQty } = await import('../src/lib/stock')
  it.each([
    [null, false, 10],
    [0, true, 1],
    [1, false, 1],
    [3, false, 3],
    [50, false, 10],
  ])('stock %s -> soldOut %s, max %s (manual and automatic alike)', (stock, sold, max) => {
    expect(isSoldOut({ stock })).toBe(sold)
    expect(maxOrderQty({ stock })).toBe(max)
  })
})

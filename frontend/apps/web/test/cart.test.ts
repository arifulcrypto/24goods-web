import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
vi.mock('../src/api', () => ({ api: {} }))
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

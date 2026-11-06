import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { CartQuote, CartQuoteLine, CheckoutLineInput } from '@24goods/shared'
import { addAmounts, feeBreakdown, isApiError } from '@24goods/shared'
import { api } from '../api'

export const CART_MAX_LINES = 20
export const CART_MAX_QTY = 99
const KEY = 'kesiv.cart.v1'

export interface CartLine {
  product_id: string
  qty: number
  buyer_input?: string
  selected: boolean
}

function load(): CartLine[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    if (!Array.isArray(raw)) return []
    const seen = new Set<string>()
    const out: CartLine[] = []
    for (const x of raw) {
      if (!x || typeof x !== 'object') continue
      const l = x as Partial<CartLine>
      if (typeof l.product_id !== 'string' || !l.product_id || seen.has(l.product_id)) continue
      seen.add(l.product_id)
      const qty = Number.isInteger(l.qty) ? Math.min(CART_MAX_QTY, Math.max(1, l.qty as number)) : 1
      out.push({
        product_id: l.product_id,
        qty,
        selected: l.selected !== false,
        ...(typeof l.buyer_input === 'string' && l.buyer_input ? { buyer_input: l.buyer_input.slice(0, 4000) } : {}),
      })
      if (out.length >= CART_MAX_LINES) break
    }
    return out
  } catch {
    return []
  }
}

export const useCartStore = defineStore('cart', () => {
  const lines = ref<CartLine[]>(load())
  const quote = ref<CartQuote | null>(null)
  const quoting = ref(false)
  const quoteError = ref(false)
  let seq = 0

  function persist() {
    try {
      localStorage.setItem(KEY, JSON.stringify(lines.value))
    } catch {
      /* storage blocked: the cart lives for this tab only */
    }
  }

  const count = computed(() => lines.value.reduce((n, l) => n + l.qty, 0))
  const quoteByProduct = computed(() => {
    const m = new Map<string, CartQuoteLine>()
    for (const q of quote.value?.lines ?? []) m.set(q.product_id, q)
    return m
  })
  /** v5 §1: a line the server says cannot be bought (archived / unpublished / out of stock). */
  function isUnavailable(q: CartQuoteLine | undefined): boolean {
    return !!q && (!q.available || !!q.unavailable_reason)
  }
  const availableLines = computed(() => lines.value.filter((l) => !isUnavailable(quoteByProduct.value.get(l.product_id))))
  const unavailableLines = computed(() => lines.value.filter((l) => isUnavailable(quoteByProduct.value.get(l.product_id))))
  /** Selected lines that the server says can be bought (unavailable ones are never checked out). */
  const payable = computed(() =>
    lines.value.filter((l) => {
      const q = quoteByProduct.value.get(l.product_id)
      return l.selected && q?.available === true && !q.unavailable_reason
    }),
  )
  const payableCount = computed(() => payable.value.length)
  const currency = computed(() => quote.value?.currency ?? 'CNY')
  /** Display-only totals for the selected, available lines (the server recomputes at checkout). */
  function totals(permille: number, discountMinor = 0): { subtotal: string; fee: string; total: string; discount: string } {
    let sub = '0.00'
    for (const l of payable.value) {
      const q = quoteByProduct.value.get(l.product_id)
      if (q) sub = addAmounts(sub, lineSubtotal(l, q)) ?? sub
    }
    const b = feeBreakdown(sub, discountMinor, permille)
    return { subtotal: b.subtotal, fee: b.fee, total: b.total, discount: b.discount }
  }
  function lineSubtotal(l: CartLine, q: CartQuoteLine): string {
    // unit price × current qty (the quote may be older than the last stepper tap)
    const unit = q.unit_price
    const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(unit)
    if (!m) return q.line_subtotal
    const minor = BigInt(m[1]!) * 100n + BigInt((m[2] ?? '').padEnd(2, '0') || '0')
    const t = minor * BigInt(l.qty)
    return `${t / 100n}.${(t % 100n).toString().padStart(2, '0')}`
  }

  /** Returns false when the cart is full. */
  function add(productId: string, qty = 1, buyerInput?: string | null): boolean {
    const i = lines.value.findIndex((l) => l.product_id === productId)
    if (i >= 0) {
      const l = lines.value[i]!
      l.qty = Math.min(CART_MAX_QTY, l.qty + qty)
      l.selected = true
      if (buyerInput) l.buyer_input = buyerInput
    } else {
      if (lines.value.length >= CART_MAX_LINES) return false
      lines.value.push({
        product_id: productId,
        qty: Math.min(CART_MAX_QTY, Math.max(1, qty)),
        selected: true,
        ...(buyerInput ? { buyer_input: buyerInput } : {}),
      })
    }
    persist()
    return true
  }
  function setQty(productId: string, qty: number) {
    const l = lines.value.find((x) => x.product_id === productId)
    if (!l) return
    l.qty = Math.min(CART_MAX_QTY, Math.max(1, Math.floor(qty)))
    persist()
  }
  function setInput(productId: string, value: string) {
    const l = lines.value.find((x) => x.product_id === productId)
    if (!l) return
    l.buyer_input = value
    persist()
  }
  function setSelected(productId: string, v: boolean) {
    const l = lines.value.find((x) => x.product_id === productId)
    if (l) l.selected = v
    persist()
  }
  function selectAll(v: boolean) {
    lines.value.forEach((l) => (l.selected = v))
    persist()
  }
  function remove(productId: string) {
    lines.value = lines.value.filter((l) => l.product_id !== productId)
    persist()
  }
  /** After a successful checkout: drop exactly the lines that were bought. */
  function removeMany(ids: readonly string[]) {
    const s = new Set(ids)
    lines.value = lines.value.filter((l) => !s.has(l.product_id))
    persist()
  }

  function toInputs(ls: readonly CartLine[]): CheckoutLineInput[] {
    return ls.map((l) => ({
      product_id: l.product_id,
      qty: l.qty,
      ...(l.buyer_input ? { buyer_input: l.buyer_input } : {}),
    }))
  }

  async function refreshQuote(): Promise<void> {
    if (!lines.value.length) {
      quote.value = null
      return
    }
    const mine = ++seq
    quoting.value = true
    quoteError.value = false
    try {
      const q = await api.cartQuote(toInputs(lines.value))
      if (mine !== seq) return
      quote.value = q
      // clamp quantities to what the server allows
      let changed = false
      for (const ql of q.lines) {
        const l = lines.value.find((x) => x.product_id === ql.product_id)
        if (l && ql.available && ql.max_qty > 0 && l.qty > ql.max_qty) {
          l.qty = ql.max_qty
          changed = true
        }
      }
      if (changed) persist()
    } catch (e) {
      if (mine !== seq) return
      quoteError.value = true
      // an unknown product id (deleted) is a validation/not_found error: do not wedge the cart
      if (isApiError(e) && e.code === 'not_found') quote.value = null
    } finally {
      if (mine === seq) quoting.value = false
    }
  }

  return {
    lines,
    quote,
    quoting,
    quoteError,
    count,
    quoteByProduct,
    payable,
    availableLines,
    unavailableLines,
    payableCount,
    currency,
    totals,
    lineSubtotal,
    toInputs,
    add,
    setQty,
    setInput,
    setSelected,
    selectAll,
    remove,
    removeMany,
    refreshQuote,
  }
})

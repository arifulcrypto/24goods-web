import { describe, expect, it } from 'vitest'
import {
  afterSalesPresetFor,
  formatLabels,
  isValidRedeemUrl,
  joinFormat,
  lineExpiry,
  presetFor,
  splitCard,
} from '../src/lib/delivery'
import { createSeenSet, createThrottle, isRelevant, titleWithCount } from '../src/lib/notifyPolicy'
import { availableActions, isOpenStatus, isValidRefundReference, needsConfirm, validateAction } from '../src/lib/afterSales'
import { buildCardQuery, canVoid, cleanLines, countLines, emptyCardFilters } from '../src/lib/cards'

describe('delivery format split preview', () => {
  const labels = ['Account', 'Password', '2FA', 'Notes']
  it('splits by ---- into labelled fields', () => {
    expect(splitCard('a@x.com----pw----TOTP----hi', labels, '----')).toEqual([
      { label: 'Account', value: 'a@x.com', extra: false },
      { label: 'Password', value: 'pw', extra: false },
      { label: '2FA', value: 'TOTP', extra: false },
      { label: 'Notes', value: 'hi', extra: false },
    ])
  })
  it('labels extra values "Field N" and handles fewer values', () => {
    const r = splitCard('u|p|x|y', ['User', 'Pass'], '|', (n) => `F${n}`)
    expect(r.map((f) => f.label)).toEqual(['User', 'Pass', 'F3', 'F4'])
    expect(r[3]!.extra).toBe(true)
    expect(splitCard('only', ['User', 'Pass'], '|')).toEqual([{ label: 'User', value: 'only', extra: false }])
  })
  it('one block when there is no format or a single label', () => {
    expect(splitCard('AAAA----BBBB', [], '----')).toEqual([{ label: 'Field 1', value: 'AAAA----BBBB', extra: true }])
    expect(splitCard('AAAA----BBBB', ['Card code'], '----')).toEqual([{ label: 'Card code', value: 'AAAA----BBBB', extra: false }])
  })
  it('supports tab, comma and colon separators', () => {
    expect(splitCard('a\tb', ['x', 'y'], '\t').map((f) => f.value)).toEqual(['a', 'b'])
    expect(splitCard('a, b', ['x', 'y'], ',').map((f) => f.value)).toEqual(['a', 'b'])
    expect(splitCard('a:b', ['x', 'y'], ':').map((f) => f.value)).toEqual(['a', 'b'])
  })
  it('strips the per-line |exp= import suffix and parses it', () => {
    expect(splitCard('a----b|exp=2026-12-31', ['x', 'y'], '----').map((f) => f.value)).toEqual(['a', 'b'])
    expect(lineExpiry('CODE|exp=2026-12-31')).toBe('2026-12-31')
    expect(lineExpiry('CODE|exp=2026-02-30')).toBeNull()
    expect(lineExpiry('CODE')).toBeNull()
  })
  it('round-trips labels and recognises presets', () => {
    expect(formatLabels('Account | Password', '|')).toEqual(['Account', 'Password'])
    expect(joinFormat(['Account', ' ', 'Password'], '|')).toBe('Account|Password')
    expect(joinFormat([], '|')).toBeNull()
    expect(presetFor(['account', 'password'])).toBe('account')
    expect(presetFor(['Foo'])).toBe('custom')
  })
  it('accepts only https redeem URLs', () => {
    expect(isValidRedeemUrl('')).toBe(true)
    expect(isValidRedeemUrl('https://redeem.example.com/x?a=1')).toBe(true)
    expect(isValidRedeemUrl('http://example.com')).toBe(false)
    expect(isValidRedeemUrl('javascript:alert(1)')).toBe(false)
    expect(isValidRedeemUrl('https://user:pw@example.com')).toBe(false)
    expect(isValidRedeemUrl('example.com')).toBe(false)
  })
  it('maps after-sales hours to presets', () => {
    expect(afterSalesPresetFor(null)).toBe('default')
    expect(afterSalesPresetFor(0)).toBe('none')
    expect(afterSalesPresetFor(168)).toBe('d7')
    expect(afterSalesPresetFor(5)).toBe('custom')
  })
})

describe('notification policy', () => {
  it('fires when hidden, or for another conversation', () => {
    const ev = { kind: 'message' as const, conversationId: 'c1' }
    expect(isRelevant(ev, { hidden: true, openConversationId: 'c1' })).toBe(true)
    expect(isRelevant(ev, { hidden: false, openConversationId: 'c1' })).toBe(false)
    expect(isRelevant(ev, { hidden: false, openConversationId: 'c2' })).toBe(true)
    expect(isRelevant(ev, { hidden: false, openConversationId: null })).toBe(true)
  })
  it('orders and tickets fire while visible unless that order is open', () => {
    expect(isRelevant({ kind: 'order', orderId: 'o1' }, { hidden: false, openConversationId: null, openOrderId: 'o1' })).toBe(false)
    expect(isRelevant({ kind: 'order', orderId: 'o1' }, { hidden: false, openConversationId: null })).toBe(true)
    expect(isRelevant({ kind: 'after_sales' }, { hidden: false, openConversationId: 'c1' })).toBe(true)
  })
  it('throttles to one per 4 s', () => {
    let now = 1000
    const th = createThrottle(4000, () => now)
    expect(th.take()).toBe(true)
    now += 3999
    expect(th.take()).toBe(false)
    now += 1
    expect(th.take()).toBe(true)
    th.reset()
    expect(th.take()).toBe(true)
  })
  it('formats the title counter', () => {
    expect(titleWithCount('Kesiv Console', 0)).toBe('Kesiv Console')
    expect(titleWithCount('Kesiv Console', 3)).toBe('(3) Kesiv Console')
    expect(titleWithCount('Kesiv Console', 150)).toBe('(99+) Kesiv Console')
  })
  it('dedupes ids with a bounded set', () => {
    const s = createSeenSet(2)
    expect(s.first('a')).toBe(true)
    expect(s.first('a')).toBe(false)
    s.first('b')
    s.first('c')
    expect(s.first('a')).toBe(true) // evicted
  })
})

describe('after-sales action availability', () => {
  it('by status', () => {
    expect(availableActions('open')).toEqual(['processing', 'replace', 'refund', 'reject', 'resolve'])
    expect(availableActions('processing')).not.toContain('processing')
    expect(availableActions('open', 'agent')).toEqual(['processing', 'replace', 'reject', 'resolve'])
    for (const s of ['replaced', 'refunded', 'rejected', 'resolved'] as const) expect(availableActions(s)).toEqual([])
    expect(isOpenStatus('processing')).toBe(true)
    expect(isOpenStatus('replaced')).toBe(false)
  })
  it('validation and confirmation', () => {
    expect(validateAction('reject', { note: ' ' }, 'manual')).toBe('afterSales.v.reasonRequired')
    expect(validateAction('reject', { note: 'dup' }, 'manual')).toBeNull()
    expect(validateAction('replace', {}, 'manual')).toBe('afterSales.v.deliveryRequired')
    expect(validateAction('replace', {}, 'auto_card')).toBeNull()
    expect(validateAction('refund', {}, 'manual')).toBe('afterSales.v.referenceRequired')
    expect(validateAction('refund', { reference: ' ab ' }, 'manual')).toBe('afterSales.v.referenceRequired')
    expect(validateAction('refund', { reference: 'TXN-1' }, 'manual')).toBeNull()
    expect(isValidRefundReference('x'.repeat(201))).toBe(false)
    expect(needsConfirm('refund')).toBe(true)
    expect(needsConfirm('resolve')).toBe(false)
  })
})

describe('card filter query building', () => {
  it('omits empty filters', () => {
    expect(buildCardQuery(emptyCardFilters(), 1, 20)).toEqual({ page: 1, page_size: 20 })
  })
  it('includes and sanitises set filters', () => {
    expect(
      buildCardQuery({ status: 'available', batch_id: 'b1', expiring_days: 7.6, order_no: ' G2026-001 ' }, 2, 50),
    ).toEqual({ page: 2, page_size: 50, status: 'available', batch_id: 'b1', expiring_days: 7, order_no: 'G2026-001' })
    expect(buildCardQuery({ ...emptyCardFilters(), expiring_days: 9999 }, 1, 20).expiring_days).toBe(365)
    expect(buildCardQuery({ ...emptyCardFilters(), expiring_days: 0 }, 1, 20)).not.toHaveProperty('expiring_days')
    expect(buildCardQuery({ ...emptyCardFilters(), order_no: 'x y/../' }, 1, 20)).not.toHaveProperty('order_no')
  })
  it('line helpers', () => {
    expect(cleanLines(' a \n\nb\r\na\n')).toEqual(['a', 'b'])
    expect(countLines('a\n\n b \nc')).toBe(3)
    expect(canVoid('available')).toBe(true)
    expect(canVoid('sold')).toBe(false)
  })
})

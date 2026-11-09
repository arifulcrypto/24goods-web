import { describe, expect, it } from 'vitest'
import { DEFAULT_ORDER_ALERTS, createSeenSet, mergeOrderAlerts, orderAlertKind } from '../src/lib/notifyPolicy'

const NOW = Date.parse('2026-11-09T10:00:00Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()

describe('order alert toggles', () => {
  it('defaults: created/paid/cancelled on, expired off', () => {
    expect(DEFAULT_ORDER_ALERTS).toEqual({ created: true, paid: true, cancelled: true, expired: false })
    expect(mergeOrderAlerts(null)).toEqual(DEFAULT_ORDER_ALERTS)
  })
  it('merges stored values and ignores junk', () => {
    expect(mergeOrderAlerts({ created: false, expired: true, paid: 'yes', extra: 1 })).toEqual({
      created: false,
      paid: true,
      cancelled: true,
      expired: true,
    })
  })
})

describe('order event mapping', () => {
  it('maps status to one alert per order and kind', () => {
    const seen = createSeenSet()
    const base = { id: 'o1', created_at: ago(1000), paid_at: null as string | null }
    expect(orderAlertKind({ ...base, status: 'pending_payment' }, seen, NOW)).toBe('created')
    expect(orderAlertKind({ ...base, status: 'pending_payment' }, seen, NOW)).toBeNull()
    const paid = { ...base, paid_at: ago(500) }
    expect(orderAlertKind({ ...paid, status: 'paid' }, seen, NOW)).toBe('paid')
    // paid -> delivered of the same order does not alert again
    expect(orderAlertKind({ ...paid, status: 'delivered' }, seen, NOW)).toBeNull()
    expect(orderAlertKind({ ...base, id: 'o2', status: 'cancelled' }, seen, NOW)).toBe('cancelled')
    expect(orderAlertKind({ ...base, id: 'o3', status: 'expired' }, seen, NOW)).toBe('expired')
  })
  it('paid covers auto delivery and ignores stale or re-broadcast orders', () => {
    const seen = createSeenSet()
    expect(orderAlertKind({ id: 'a', status: 'delivered', paid_at: ago(1000) }, seen, NOW)).toBe('paid')
    expect(orderAlertKind({ id: 'b', status: 'completed', paid_at: ago(3 * 3600e3) }, seen, NOW)).toBeNull()
    expect(orderAlertKind({ id: 'c', status: 'pending_payment', created_at: ago(3600e3) }, seen, NOW)).toBeNull()
  })
})

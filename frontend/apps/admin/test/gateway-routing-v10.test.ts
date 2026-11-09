import { describe, expect, it } from 'vitest'
import type { PaymentGatewayAdmin } from '@24goods/shared'
import { byPriority, formatCountdown, moveId, shouldPoll, testPhase, tryOrder, validTestAmount } from '../src/lib/gatewayRouting'

const gw = (id: string, priority: number, created = '2026-01-01'): PaymentGatewayAdmin => ({
  id, label: id, gateway: 'https://x.example', merchant_id: '1', mode: 'qr', active: false, enabled: true,
  priority, in_use: false, last_ok_at: null, last_fail_at: null, last_fail_reason: null, created_at: created,
})

describe('gateway priority list (v10 §B)', () => {
  it('sorts by priority, then creation time', () => {
    expect(byPriority([gw('b', 20), gw('a', 10), gw('c', 10, '2025-01-01')]).map((g) => g.id)).toEqual(['c', 'a', 'b'])
  })
  it('moves one place up/down and stops at the ends', () => {
    const ids = ['a', 'b', 'c']
    expect(moveId(ids, 'b', -1)).toEqual(['b', 'a', 'c'])
    expect(moveId(ids, 'b', 1)).toEqual(['a', 'c', 'b'])
    expect(moveId(ids, 'a', -1)).toBe(ids)
    expect(moveId(ids, 'c', 1)).toBe(ids)
    expect(moveId(ids, 'zzz', 1)).toBe(ids)
    expect(ids).toEqual(['a', 'b', 'c'])
  })
  it('numbers the order the next payment tries; disabled gateways have none', () => {
    expect(tryOrder(['b', 'a'], 'b')).toBe(1)
    expect(tryOrder(['b', 'a'], 'a')).toBe(2)
    expect(tryOrder(['b', 'a'], 'x')).toBeNull()
  })
})

describe('test payment dialog state (v10 §C)', () => {
  it('derives the phase from status and countdown', () => {
    expect(testPhase(null, 0)).toBe('waiting')
    expect(testPhase({ status: 'pending' }, 30)).toBe('waiting')
    expect(testPhase({ status: 'pending' }, 0)).toBe('expired')
    expect(testPhase({ status: 'expired' }, 99)).toBe('expired')
    expect(testPhase({ status: 'paid' }, 0)).toBe('paid')
    expect(testPhase({ status: 'failed' }, 99)).toBe('failed')
  })
  it('polls only while waiting', () => {
    expect(shouldPoll('waiting')).toBe(true)
    for (const p of ['paid', 'expired', 'failed'] as const) expect(shouldPoll(p)).toBe(false)
  })
  it('formats the countdown', () => {
    expect(formatCountdown(599)).toBe('9:59')
    expect(formatCountdown(61)).toBe('1:01')
    expect(formatCountdown(-5)).toBe('0:00')
  })
  it('validates the amount like the server (0.01..10.00, 2 decimals)', () => {
    for (const ok of ['0.01', '1', '9.99', '10', '10.00', '0.5']) expect(validTestAmount(ok), ok).toBe(true)
    for (const bad of ['0', '0.00', '10.01', '-1', '0.001', 'abc', '', '1e1', '11']) expect(validTestAmount(bad), bad).toBe(false)
  })
})

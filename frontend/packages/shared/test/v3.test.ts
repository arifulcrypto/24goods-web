import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import {
  LONG_TEXT_ASK,
  LONG_TEXT_FORCE,
  addLookup,
  addRecent,
  daysLeft,
  dismissAnnouncement,
  getLookups,
  getRecent,
  goBack,
  isAnnouncementDismissed,
  leadingZeroBits,
  longTextDecision,
  messageFilename,
  searchNonce,
  sha256Sync,
  solvePow,
  splitCard,
  type RouterLike,
} from '../src'

function memStore() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  }
}
const sha = (s: string) => new Uint8Array(createHash('sha256').update(s, 'utf8').digest())

describe('pow', () => {
  it('counts leading zero bits', () => {
    expect(leadingZeroBits(new Uint8Array([0xff]))).toBe(0)
    expect(leadingZeroBits(new Uint8Array([0x7f]))).toBe(1)
    expect(leadingZeroBits(new Uint8Array([0x00, 0x10]))).toBe(11)
    expect(leadingZeroBits(new Uint8Array([0x00, 0x00]))).toBe(16)
    expect(leadingZeroBits(new Uint8Array([0x01]))).toBe(7)
  })
  it('JS sha256 matches node crypto', () => {
    for (const s of ['', 'abc', 'x'.repeat(55), 'x'.repeat(56), 'x'.repeat(64), 'challenge:12345', '可信:1']) {
      expect(Array.from(sha256Sync(s))).toEqual(Array.from(sha(s)))
    }
  })
  it('finds a valid nonce (sync + webcrypto paths)', async () => {
    for (const forceSync of [true, false]) {
      const n = await searchNonce({ challenge: 'abc.def', bits: 10, forceSync })
      expect(n).not.toBeNull()
      expect(leadingZeroBits(sha(`abc.def:${n}`))).toBeGreaterThanOrEqual(10)
    }
  })
  it('solvePow returns a decimal-string nonce (main-thread fallback)', async () => {
    const s = await solvePow({ challenge: 'zz', bits: 8, expires_at: 0 }, { noWorker: true })
    expect(s.nonce).toMatch(/^\d+$/)
    expect(leadingZeroBits(sha(`zz:${s.nonce}`))).toBeGreaterThanOrEqual(8)
  })
})

describe('goBack', () => {
  const mk = (back: unknown) => {
    const calls: string[] = []
    const r: RouterLike = {
      options: { history: { state: { back } } },
      back: () => void calls.push('back'),
      replace: (to) => void calls.push(`replace:${to}`),
    }
    return { r, calls }
  }
  it('goes back when the previous entry is in-app', () => {
    const { r, calls } = mk('/p/1')
    goBack(r, '/')
    expect(calls).toEqual(['back'])
  })
  it('replaces with the fallback when there is no in-app history (payment return)', () => {
    for (const b of [null, undefined, '']) {
      const { r, calls } = mk(b)
      goBack(r, '/orders')
      expect(calls).toEqual(['replace:/orders'])
    }
  })
})

describe('splitCard', () => {
  const info = { format_labels: ['Account', 'Password', '2FA'], separator: '----' }
  it('labels fields and flags extras', () => {
    const f = splitCard('a----b----c----d', info)
    expect(f.map((x) => x.value)).toEqual(['a', 'b', 'c', 'd'])
    expect(f.map((x) => x.label)).toEqual(['Account', 'Password', '2FA', ''])
    expect(f.map((x) => x.extra)).toEqual([false, false, false, true])
  })
  it('single block without a format', () => {
    expect(splitCard('a----b', null)).toHaveLength(1)
    expect(splitCard('a----b', { format_labels: [], separator: '----' })[0]!.value).toBe('a----b')
  })
  it('fewer fields than labels', () => {
    expect(splitCard('a----b', info)).toHaveLength(2)
  })
})

describe('recently viewed / lookups / announcement', () => {
  it('dedupes, newest first, capped at 20', () => {
    const s = memStore()
    for (let i = 0; i < 25; i++) addRecent(`p${i}`, s, i)
    addRecent('p10', s, 99)
    const r = getRecent(s)
    expect(r).toHaveLength(20)
    expect(r[0]!.id).toBe('p10')
    expect(r.filter((e) => e.id === 'p10')).toHaveLength(1)
  })
  it('survives broken storage', () => {
    const bad = {
      getItem: () => {
        throw new Error('x')
      },
      setItem: () => {
        throw new Error('x')
      },
      removeItem: () => undefined,
    }
    expect(getRecent(bad)).toEqual([])
    expect(() => addRecent('a', bad)).not.toThrow()
    expect(isAnnouncementDismissed(1, bad)).toBe(false)
  })
  it('keeps the last 5 order numbers only', () => {
    const s = memStore()
    for (let i = 0; i < 7; i++) addLookup(`NO${i}`, s)
    expect(getLookups(s)).toEqual(['NO6', 'NO5', 'NO4', 'NO3', 'NO2'])
  })
  it('announcement dismissal is per version', () => {
    const s = memStore()
    dismissAnnouncement(3, s)
    expect(isAnnouncementDismissed(3, s)).toBe(true)
    expect(isAnnouncementDismissed(4, s)).toBe(false)
  })
})

describe('long text', () => {
  it('thresholds', () => {
    expect(longTextDecision('a'.repeat(LONG_TEXT_ASK))).toBe('text')
    expect(longTextDecision('a'.repeat(LONG_TEXT_ASK + 1))).toBe('ask')
    expect(longTextDecision('a'.repeat(LONG_TEXT_FORCE))).toBe('ask')
    expect(longTextDecision('a'.repeat(LONG_TEXT_FORCE + 1))).toBe('file')
  })
  it('filename', () => {
    expect(messageFilename(new Date(2026, 10, 5, 9, 7))).toBe('message-20261105-0907.txt')
  })
  it('daysLeft', () => {
    const now = Date.parse('2026-11-05T00:00:00Z')
    expect(daysLeft('2026-11-08T00:00:00Z', now)).toBe(3)
    expect(daysLeft('2026-11-01T00:00:00Z', now)).toBe(0)
    expect(daysLeft(null, now)).toBeNull()
  })
})

import { afterSalesBadge, canRequestAfterSales, formatLabels } from '../src'
describe('after-sales helpers', () => {
  it('badge', () => {
    expect(afterSalesBadge(24)).toEqual({ n: 24, unit: 'h' })
    expect(afterSalesBadge(672)).toEqual({ n: 28, unit: 'd' })
    expect(afterSalesBadge(0)).toBeNull()
    expect(afterSalesBadge(null)).toBeNull()
  })
  it('eligibility', () => {
    const future = new Date(Date.now() + 86_400_000).toISOString()
    expect(canRequestAfterSales({ status: 'delivered', after_sales_until: future })).toBe(true)
    expect(canRequestAfterSales({ status: 'paid', after_sales_until: future })).toBe(false)
    expect(canRequestAfterSales({ status: 'completed', after_sales_until: '2020-01-01T00:00:00Z' })).toBe(false)
    expect(canRequestAfterSales({ status: 'completed', after_sales_until: future, after_sales: { status: 'open' } })).toBe(false)
    expect(canRequestAfterSales({ status: 'completed', after_sales_until: future, after_sales: { status: 'rejected' } })).toBe(true)
  })
  it('format labels', () => {
    expect(formatLabels('A----B----C', '----')).toEqual(['A', 'B', 'C'])
    expect(formatLabels(null, '|')).toEqual([])
  })
})

import { describe, expect, it } from 'vitest'
import {
  addAmounts,
  computeFee,
  permilleFromRate,
  compareAmount,
  formatCountdown,
  formatMoney,
  formatVoiceDuration,
  fromMinor,
  isValidAmount,
  moneyParts,
  multiplyAmount,
  normalizeAmount,
  toMinor,
} from '../src'

describe('money', () => {
  it('converts to/from integer minor units without float error', () => {
    expect(toMinor('19.90')).toBe(1990n)
    expect(toMinor('19.9')).toBe(1990n)
    expect(toMinor('0.07')).toBe(7n)
    expect(toMinor('1')).toBe(100n)
    expect(toMinor('1.234')).toBeNull()
    expect(toMinor('abc')).toBeNull()
    expect(fromMinor(1990n)).toBe('19.90')
    expect(fromMinor(5n)).toBe('0.05')
    expect(fromMinor(-150n)).toBe('-1.50')
    // 0.1 + 0.2 class bugs
    expect(multiplyAmount('0.10', 3)).toBe('0.30')
    expect(multiplyAmount('19.99', 7)).toBe('139.93')
  })

  it('validates and normalizes user input', () => {
    expect(isValidAmount('19.9')).toBe(true)
    expect(isValidAmount('0')).toBe(false)
    expect(isValidAmount('-1')).toBe(false)
    expect(isValidAmount('1e3')).toBe(false)
    expect(isValidAmount('1.999')).toBe(false)
    expect(normalizeAmount('5')).toBe('5.00')
    expect(compareAmount('10.00', '9.99')).toBe(1)
    expect(compareAmount('9.99', '9.99')).toBe(0)
    expect(compareAmount('x', '1')).toBeNull()
  })

  it('formats with Intl per locale', () => {
    const en = formatMoney('19.90', 'CNY', 'en')
    const zh = formatMoney('19.90', 'CNY', 'zh-CN')
    expect(en).toContain('19.90')
    expect(en).toMatch(/CN¥|¥/)
    expect(zh).toContain('¥')
    expect(zh).toContain('19.90')
    expect(formatMoney('1234567.5', 'CNY', 'en')).toContain('1,234,567.50')
    expect(formatMoney(null, 'CNY', 'en')).toBe('—')
    expect(formatMoney('garbage', 'CNY', 'en')).toBe('garbage')
  })

  it('keeps exact decimals for large amounts (string input)', () => {
    expect(formatMoney('99999999.99', 'CNY', 'en')).toContain('99,999,999.99')
  })

  it('splits currency symbol and value', () => {
    const p = moneyParts('8.50', 'CNY', 'zh-CN')
    expect(p.symbol).toContain('¥')
    expect(p.value).toBe('8.50')
  })
})

describe('durations', () => {
  it('voice duration WeChat style', () => {
    expect(formatVoiceDuration(7000)).toBe('7″')
    expect(formatVoiceDuration(200)).toBe('1″')
    expect(formatVoiceDuration(60000)).toBe('1′00″')
  })
  it('countdown', () => {
    const now = Date.parse('2026-01-01T00:00:00Z')
    expect(formatCountdown('2026-01-01T00:05:09Z', now)).toBe('05:09')
    expect(formatCountdown('2026-01-01T02:00:00Z', now)).toBe('2:00:00')
    expect(formatCountdown('2025-12-31T23:59:59Z', now)).toBeNull()
  })
})

describe('v4 fee helpers', () => {
  it('rounds the fee up to the fen', () => {
    expect(computeFee('10.00', 30)).toBe('0.30')
    expect(computeFee('0.01', 30)).toBe('0.01')
    expect(computeFee('9.99', 30)).toBe('0.30')
    expect(computeFee('10.00', 0)).toBe('0.00')
    expect(computeFee('x', 30)).toBeNull()
  })
  it('adds and parses', () => {
    expect(addAmounts('10.00', '0.30')).toBe('10.30')
    expect(permilleFromRate('3%')).toBe(30)
    expect(permilleFromRate('1.5%')).toBe(15)
    expect(permilleFromRate('abc')).toBeNull()
  })
})

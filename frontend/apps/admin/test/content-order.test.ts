import { describe, expect, it } from 'vitest'
import { hoursLabel, isValidSlug, slugify } from '../src/lib/content'
import { buildBuyerTemplate, htmlToText } from '../src/lib/orderText'

describe('content helpers', () => {
  it('slugifies titles and validates slugs', () => {
    expect(slugify('  After-Sales Policy!  ')).toBe('after-sales-policy')
    expect(slugify('售后')).toBe('')
    expect(isValidSlug('help')).toBe(true)
    expect(isValidSlug('Bad Slug')).toBe(false)
    expect(isValidSlug('a--b')).toBe(false)
    expect(isValidSlug('')).toBe(false)
  })
  it('hoursLabel prefers whole days', () => {
    expect(hoursLabel(672)).toEqual({ value: 28, unit: 'd' })
    expect(hoursLabel(30)).toEqual({ value: 30, unit: 'h' })
  })
})

describe('order text', () => {
  const labels = { order: 'Order', item: 'Item', content: 'Delivery', usage: 'How to use', redeem: 'Redeem at', afterSales: 'After-sales until', thanks: 'Thanks' }
  it('builds a buyer template and omits empty sections', () => {
    const full = buildBuyerTemplate({ orderNo: 'K1', title: 'Gmail', content: 'a@b.c----pw\n', usage: 'Log in', redeemUrl: 'https://x.test', afterSalesUntil: '2026-12-03T14:05:00Z', labels, formatDate: () => 'D' })
    expect(full).toBe('Order: K1\nItem: Gmail\n\nDelivery:\na@b.c----pw\n\nRedeem at: https://x.test\n\nHow to use:\nLog in\n\nAfter-sales until: D\n\nThanks')
    const min = buildBuyerTemplate({ orderNo: 'K1', title: 'T', content: 'c', labels, formatDate: () => '' })
    expect(min).toBe('Order: K1\nItem: T\n\nDelivery:\nc\n\nThanks')
  })
  it('htmlToText keeps text only', () => {
    expect(htmlToText('<p>Hello <b>you</b></p><script>x</script>')).toContain('Hello you')
    expect(htmlToText(null)).toBe('')
  })
})

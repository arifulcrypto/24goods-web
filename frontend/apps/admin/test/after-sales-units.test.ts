import { describe, expect, it } from 'vitest'
import { afterSalesFromForm, afterSalesToForm, afterSalesValid } from '../src/lib/delivery'

describe('after-sales unit control (v7)', () => {
  it('reads a product into unit + value', () => {
    expect(afterSalesToForm(null, null)).toEqual({ unit: 'default', value: 1 })
    expect(afterSalesToForm(null, 3)).toEqual({ unit: 'months', value: 3 })
    expect(afterSalesToForm(72, null)).toEqual({ unit: 'days', value: 3 })
    expect(afterSalesToForm(36, null)).toEqual({ unit: 'hours', value: 36 })
    expect(afterSalesToForm(0, null)).toEqual({ unit: 'hours', value: 0 })
  })
  it('writes one unit and clears the other', () => {
    expect(afterSalesFromForm('default', 1)).toEqual({ after_sales_hours: null, after_sales_months: null })
    expect(afterSalesFromForm('hours', 48)).toEqual({ after_sales_hours: 48, after_sales_months: null })
    expect(afterSalesFromForm('days', 7)).toEqual({ after_sales_hours: 168, after_sales_months: null })
    expect(afterSalesFromForm('months', 2)).toEqual({ after_sales_hours: null, after_sales_months: 2 })
  })
  it('validates ranges', () => {
    expect(afterSalesValid('months', 12)).toBe(true)
    expect(afterSalesValid('months', 13)).toBe(false)
    expect(afterSalesValid('days', 0)).toBe(false)
    expect(afterSalesValid('hours', 0)).toBe(true)
    expect(afterSalesValid('hours', 1.5)).toBe(false)
  })
})

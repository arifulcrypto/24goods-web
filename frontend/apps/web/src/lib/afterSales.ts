import { afterSalesBadge } from '@24goods/shared'

/** i18n key + count for the after-sales badge: whole months win over the hour/day badge. */
export function afterSalesLabel(
  hours: number | null | undefined,
  months: number | null | undefined,
): { key: 'product.afterSalesM1' | 'product.afterSalesM' | 'product.afterSalesH' | 'product.afterSalesD'; n: number } | null {
  if (months && months > 0) return { key: months === 1 ? 'product.afterSalesM1' : 'product.afterSalesM', n: months }
  const b = afterSalesBadge(hours)
  return b ? { key: b.unit === 'h' ? 'product.afterSalesH' : 'product.afterSalesD', n: b.n } : null
}

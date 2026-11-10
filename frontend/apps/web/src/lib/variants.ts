import type { ProductSummary, ProductVariant } from '@24goods/shared'

/** v11 W1: a product with active variants is bought through one of them. */
export function hasVariants(p: Pick<ProductSummary, 'variants'> | null | undefined): boolean {
  return !!p?.variants?.length
}

function soldOutVariant(v: ProductVariant): boolean {
  return v.stock !== null && v.stock !== undefined && v.stock <= 0
}

/** The variant to preselect: the first one in stock, else the first. */
export function defaultVariantId(variants: readonly ProductVariant[] | undefined): string | null {
  if (!variants?.length) return null
  return (variants.find((v) => !soldOutVariant(v)) ?? variants[0])!.id
}

export function findVariant(p: Pick<ProductSummary, 'variants'> | null | undefined, id: string | null): ProductVariant | null {
  return p?.variants?.find((v) => v.id === id) ?? null
}

/** The product as the selected variant presents it: price, stock and after-sales follow the variant. */
export function withVariant<T extends ProductSummary>(p: T, v: ProductVariant | null): T {
  if (!v) return p
  return {
    ...p,
    price: v.price,
    stock: v.stock,
    after_sales_hours: v.after_sales_hours ?? p.after_sales_hours,
    after_sales_months: v.after_sales_months ?? null,
    price_from: false,
  }
}

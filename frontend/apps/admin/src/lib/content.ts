/** Content (articles / pages / site settings) helpers. Pure, unit-tested. */

/** Slugs the buyer app links to directly (review-v3 §2.8). */
export const FIXED_SLUGS = ['help', 'rules', 'after-sales', 'terms', 'privacy'] as const
export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/

/** Lowercase, ascii-only slug suggestion from a title ("" when nothing usable remains). */
export function slugify(title: string): string {
  return title
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
}

export function isValidSlug(s: string): boolean {
  return s.length >= 1 && s.length <= 63 && SLUG_RE.test(s)
}

/** Hours → whole days when divisible (for "672 h (28 d)" displays). */
export function hoursLabel(h: number): { value: number; unit: 'h' | 'd' } {
  return h > 0 && h % 24 === 0 ? { value: h / 24, unit: 'd' } : { value: h, unit: 'h' }
}

/**
 * Single source of truth for every user-visible brand string (review-v3 §1).
 * Renaming the store = edit this file (+ APP_BRAND_NAME on the backend, index.html titles).
 */
export const BRAND = {
  name: 'Kesiv',
  /** Chinese name shown next to / instead of the wordmark in zh-CN. */
  nameZh: '可信',
  domain: 'kesiv.com',
  tagline: {
    en: 'Accounts, gift cards & digital services — delivered fast',
    'zh-CN': '账号 · 卡密 · 数字服务，快速交付',
  },
} as const

export type BrandLocale = keyof typeof BRAND.tagline

export function brandName(locale: string): string {
  return locale === 'zh-CN' ? `${BRAND.nameZh} ${BRAND.name}` : BRAND.name
}

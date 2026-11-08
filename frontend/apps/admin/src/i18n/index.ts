import { createI18n } from 'vue-i18n'
import { detectLocale, localeFromQuery, localePreference, mergeMessageTrees, sharedMessages, type MessageTree } from '@24goods/shared'
import en from './en'
import zhCN from './zh-CN'

type Widen<T> = T extends string ? string : { [K in keyof T]: Widen<T[K]> }
export type AdminMessages = Widen<typeof en>
export type AppLocale = 'en' | 'zh-CN'

export const adminMessages = { en: en as AdminMessages, 'zh-CN': zhCN }

export const messages = {
  en: mergeMessageTrees(sharedMessages.en as unknown as MessageTree, en as unknown as MessageTree),
  'zh-CN': mergeMessageTrees(sharedMessages['zh-CN'] as unknown as MessageTree, zhCN as unknown as MessageTree),
}

const initial = detectLocale()
// a ?lang= link must not overwrite the saved choice (only the in-app switcher persists)
if (typeof document !== 'undefined') document.documentElement.lang = initial

export const i18n = createI18n({
  legacy: false,
  locale: initial,
  fallbackLocale: 'en',
  messages,
  missingWarn: import.meta.env.DEV,
  fallbackWarn: false,
})

export function currentLocale(): AppLocale {
  return i18n.global.locale.value as AppLocale
}

if (typeof window !== 'undefined') window.addEventListener('languagechange', () => {
  if (localePreference() === 'auto' && !localeFromQuery()) {
    const l = detectLocale()
    i18n.global.locale.value = l
    document.documentElement.lang = l
  }
})

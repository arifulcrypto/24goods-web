import { createI18n } from 'vue-i18n'
import { detectLocale, mergeMessageTrees, persistLocale, sharedMessages, type MessageTree } from '@24goods/shared'
import en from './en'
import zhCN from './zh-CN'

type Widen<T> = T extends string ? string : { [K in keyof T]: Widen<T[K]> }
export type WebMessages = Widen<typeof en>
export type AppLocale = 'en' | 'zh-CN'

export const webMessages = { en: en as WebMessages, 'zh-CN': zhCN }

export const messages = {
  en: mergeMessageTrees(sharedMessages.en as unknown as MessageTree, en as unknown as MessageTree),
  'zh-CN': mergeMessageTrees(sharedMessages['zh-CN'] as unknown as MessageTree, zhCN as unknown as MessageTree),
}

const initial = detectLocale()
persistLocale(initial)

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

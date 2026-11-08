import { Locale } from 'vant'
import enUS from 'vant/es/locale/lang/en-US'
import zhCNVant from 'vant/es/locale/lang/zh-CN'
import { ref } from 'vue'
import { browserLocale, localePreference, persistLocale, type LocalePreference } from '@24goods/shared'
import { i18n, type AppLocale } from './i18n'

export function applyVantLocale(l: AppLocale): void {
  if (l === 'zh-CN') Locale.use('zh-CN', zhCNVant)
  else Locale.use('en-US', enUS)
}

export const languagePreference = ref<LocalePreference>(localePreference())

export function setLocale(preference: LocalePreference): void {
  languagePreference.value = preference
  const l = preference === 'auto' ? browserLocale() : preference
  i18n.global.locale.value = l
  persistLocale(preference)
  applyVantLocale(l)
}

window.addEventListener('languagechange', () => applyVantLocale(i18n.global.locale.value as AppLocale))

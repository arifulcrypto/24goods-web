import { Locale } from 'vant'
import enUS from 'vant/es/locale/lang/en-US'
import zhCNVant from 'vant/es/locale/lang/zh-CN'
import { persistLocale } from '@24goods/shared'
import { i18n, type AppLocale } from './i18n'

export function applyVantLocale(l: AppLocale): void {
  if (l === 'zh-CN') Locale.use('zh-CN', zhCNVant)
  else Locale.use('en-US', enUS)
}

export function setLocale(l: AppLocale): void {
  i18n.global.locale.value = l
  persistLocale(l)
  applyVantLocale(l)
}

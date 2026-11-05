import { computed } from 'vue'
import en from 'element-plus/es/locale/lang/en'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { persistLocale } from '@24goods/shared'
import { i18n, type AppLocale } from './i18n'

export const elementLocale = computed(() => (i18n.global.locale.value === 'zh-CN' ? zhCn : en))

export function setLocale(l: AppLocale): void {
  i18n.global.locale.value = l
  persistLocale(l)
}

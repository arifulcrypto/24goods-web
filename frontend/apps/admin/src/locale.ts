import { computed } from 'vue'
import en from 'element-plus/es/locale/lang/en'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { ref } from 'vue'
import { browserLocale, localePreference, persistLocale, type LocalePreference } from '@24goods/shared'
import { i18n } from './i18n'

export const elementLocale = computed(() => (i18n.global.locale.value === 'zh-CN' ? zhCn : en))

export const languagePreference = ref<LocalePreference>(localePreference())

export function setLocale(preference: LocalePreference): void {
  languagePreference.value = preference
  const l = preference === 'auto' ? browserLocale() : preference
  i18n.global.locale.value = l
  persistLocale(preference)
}

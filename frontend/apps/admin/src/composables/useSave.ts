import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toastError, toastOk } from '../ui'

/** Wraps a save action: busy flag, success toast, error toast. Returns the action's result or undefined. */
export function useSave() {
  const { t } = useI18n()
  const saving = ref(false)
  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    if (saving.value) return undefined
    saving.value = true
    try {
      const r = await fn()
      toastOk(t('site.saved'))
      return r
    } catch (e) {
      toastError(e)
      return undefined
    } finally {
      saving.value = false
    }
  }
  return { saving, run }
}

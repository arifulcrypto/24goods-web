import { errorI18nKey, isApiError, validationDetail } from '@24goods/shared'
import { i18n } from './i18n'

/** Translate an error by contract code and show it (ElMessage is auto-imported with its CSS). */
export function toastError(e: unknown): void {
  if (e instanceof DOMException && e.name === 'AbortError') return
  if (isApiError(e) && e.code === 'unauthorized') return
  if (isApiError(e) && (e.code === 'ip_blocked' || e.code === 'account_suspended')) return // full-page screen
  // A bare "Please check your input" on a 12-field form is unusable: the API already says which
  // field and why, so append it. Staff-only console, so the English constraint text is acceptable
  // next to the translated headline; buyers never see this path.
  const detail = validationDetail(e)
  const message = i18n.global.t(errorI18nKey(e)) + (detail ? ` — ${detail}` : '')
  ElMessage.error({ message, grouping: true })
}
export function toastOk(msg: string): void {
  ElMessage.success({ message: msg, grouping: true })
}
export async function confirmBox(message: string, title?: string): Promise<boolean> {
  const t = i18n.global.t
  try {
    await ElMessageBox.confirm(message, title ?? t('common.confirm'), {
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
}

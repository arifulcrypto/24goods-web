/**
 * Vant function APIs (toast/dialog/image preview). Imported explicitly with their styles —
 * unplugin-vue-components only covers template components.
 */
import { showToast, showSuccessToast, showFailToast, showLoadingToast, closeToast, showConfirmDialog, showImagePreview } from 'vant'
import 'vant/es/toast/style'
import 'vant/es/dialog/style'
import 'vant/es/image-preview/style'
import { errorI18nKey, isApiError } from '@24goods/shared'
import { i18n } from './i18n'

export { showToast, showSuccessToast, showFailToast, showLoadingToast, closeToast, showConfirmDialog, showImagePreview }

/** Translate an error by its contract code and show it as a toast. */
export function toastError(e: unknown): void {
  if (e instanceof DOMException && e.name === 'AbortError') return
  const t = i18n.global.t
  if (isApiError(e) && e.code === 'unauthorized') return // global 401 handler redirects
  if (isApiError(e) && e.code === 'too_frequent') return // global 429 toast already shown
  if (isApiError(e) && (e.code === 'ip_blocked' || e.code === 'account_suspended')) return // full-page screen
  showFailToast({ message: t(errorI18nKey(e)), wordBreak: 'break-word' })
}

export function errorText(e: unknown): string {
  return i18n.global.t(errorI18nKey(e))
}

/** Promise-based confirm; resolves false on cancel. */
export async function confirm(title: string, message: string): Promise<boolean> {
  const t = i18n.global.t
  try {
    await showConfirmDialog({
      title,
      message,
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
    })
    return true
  } catch {
    return false
  }
}

/** SmartText URL chip: show the FULL url and let the user decide (review-v4 §7). */
export async function confirmOpenLink(url: string): Promise<boolean> {
  const t = i18n.global.t
  try {
    await showConfirmDialog({
      title: t('chat.smart.openLinkTitle'),
      message: `${url}\n\n${t('chat.smart.openLinkWarn')}`,
      confirmButtonText: t('chat.smart.open'),
      cancelButtonText: t('common.cancel'),
      className: 'link-confirm',
    })
    return true
  } catch {
    return false
  }
}

/** Pay / cancel flows shared by My Orders and the order detail page (review-v4 §2, §3). */
import { errorExtra, isApiError, safePayUrl, type Order } from '@24goods/shared'
import { api } from '../api'
import { useSiteStore } from '../stores/site'
import { i18n } from '../i18n'
import { closeToast, confirm, showLoadingToast, showSuccessToast, showToast, toastError } from '../ui'

const t = (k: string, p?: Record<string, unknown>) => i18n.global.t(k, p ?? {})

/** Channel the buyer picked (Alipay when channels are not loaded / only one is enabled). */
async function channelId(): Promise<string | undefined> {
  const site = useSiteStore()
  await site.loadChannels()
  return site.channel?.id
}

function go(payUrl: string): void {
  const safe = safePayUrl(payUrl)
  if (!safe) throw new Error('unsafe pay_url')
  showLoadingToast({ message: t('order.paying'), forbidClick: true, duration: 0 })
  window.location.assign(safe)
}

export { channelId }

export async function payCheckout(no: string): Promise<void> {
  try {
    go((await api.checkout.pay(no, await channelId())).pay_url)
  } catch (e) {
    closeToast()
    toastError(e)
    throw e
  }
}

/** Pays a single order; a member of a combined checkout is paid through the checkout (409 pay_via_checkout). */
export async function payOrder(o: Pick<Order, 'id' | 'checkout_no'>): Promise<void> {
  if (o.checkout_no) return payCheckout(o.checkout_no)
  try {
    go((await api.orders.pay(o.id, await channelId())).pay_url)
  } catch (e) {
    closeToast()
    if (isApiError(e) && e.code === 'pay_via_checkout') {
      const no = errorExtra(e).checkout_no
      if (typeof no === 'string' && no) return payCheckout(no)
    }
    toastError(e)
    throw e
  }
}

/** Confirm + cancel. Resolves true when the order (or its whole checkout) was cancelled. */
export async function cancelPending(o: Pick<Order, 'id' | 'checkout_no'>, memberCount = 0): Promise<boolean> {
  const message =
    o.checkout_no && memberCount > 1 ? t('cancel.messageCheckout', { n: memberCount }) : t('cancel.message')
  if (!(await confirm(t('cancel.title'), message))) return false
  try {
    if (o.checkout_no) await api.checkout.cancel(o.checkout_no)
    else await api.orders.cancel(o.id)
    showSuccessToast(t('cancel.done'))
    return true
  } catch (e) {
    if (isApiError(e) && e.code === 'already_paid') showToast(t('cancel.alreadyPaid'))
    else toastError(e)
    return false
  }
}

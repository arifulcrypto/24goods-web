import { ApiClient, createBuyerApi, createRateLimitNotifier, storefrontHeaders, type ApiError } from '@24goods/shared'
import { siteAccessBlocked, verifiedPay } from './lib/securityChallenge'
import { currentLocale, i18n } from './i18n'
import { showFailToast } from './ui'

type UnauthorizedHandler = (e: ApiError) => void
let onUnauthorized: UnauthorizedHandler = () => undefined
export function setUnauthorizedHandler(fn: UnauthorizedHandler): void {
  onUnauthorized = fn
}

export const client = new ApiClient({
  baseUrl: '/api/v1',
  onError: (e) => { if(e.code === 'site_visit_required') siteAccessBlocked.value=true },
  getLocale: () => currentLocale(),
  // v5 §7: `?store=slug` (remembered in sessionStorage) is sent as X-Storefront on every request
  getHeaders: () => storefrontHeaders(),
  onUnauthorized: (e) => onUnauthorized(e),
  // v4 §10: global access-frequency limit → one toast per few seconds
  onRateLimited: createRateLimitNotifier(() =>
    showFailToast({ message: i18n.global.t('errors.too_frequent'), wordBreak: 'break-word' }),
  ),
})
export const api = createBuyerApi(client)

api.orders.pay = (ref, channel) => verifiedPay(client, 'order', ref, channel)
api.checkout.pay = (ref, channel) => verifiedPay(client, 'checkout', ref, channel)

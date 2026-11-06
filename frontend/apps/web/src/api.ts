import { ApiClient, createBuyerApi, createRateLimitNotifier, type ApiError } from '@24goods/shared'
import { currentLocale, i18n } from './i18n'
import { showFailToast } from './ui'

type UnauthorizedHandler = (e: ApiError) => void
let onUnauthorized: UnauthorizedHandler = () => undefined
export function setUnauthorizedHandler(fn: UnauthorizedHandler): void {
  onUnauthorized = fn
}

export const client = new ApiClient({
  baseUrl: '/api/v1',
  getLocale: () => currentLocale(),
  onUnauthorized: (e) => onUnauthorized(e),
  // v4 §10: global access-frequency limit → one toast per few seconds
  onRateLimited: createRateLimitNotifier(() =>
    showFailToast({ message: i18n.global.t('errors.too_frequent'), wordBreak: 'break-word' }),
  ),
})
export const api = createBuyerApi(client)

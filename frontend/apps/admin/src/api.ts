import { ApiClient, createAdminApi, createRateLimitNotifier, type ApiError } from '@24goods/shared'
import { currentLocale, i18n } from './i18n'
import { createAdminV3Api } from './api-v3'
import { createAdminV4Api } from './api-v4'
import { createAdminV5Api } from './api-v5'

type UnauthorizedHandler = (e: ApiError) => void
let onUnauthorized: UnauthorizedHandler = () => undefined
export function setUnauthorizedHandler(fn: UnauthorizedHandler): void {
  onUnauthorized = fn
}

export const client = new ApiClient({
  baseUrl: '/api/v1',
  getLocale: () => currentLocale(),
  onUnauthorized: (e) => onUnauthorized(e),
  onRateLimited: createRateLimitNotifier(() =>
    ElMessage.error({ message: i18n.global.t('errors.too_frequent'), grouping: true }),
  ),
})
export const api = createAdminApi(client)
/** review-v3 §12 admin endpoints (api-v3.ts). */
export const apiV3 = createAdminV3Api(client)
/** review-v4 §12 admin endpoints (api-v4.ts). */
export const apiV4 = createAdminV4Api(client)
/** review-v5 §12 admin endpoints (api-v5.ts). */
export const apiV5 = createAdminV5Api(client)

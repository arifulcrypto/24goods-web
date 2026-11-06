import { ApiClient, createAdminApi, type ApiError } from '@24goods/shared'
import { currentLocale } from './i18n'
import { createAdminV3Api } from './api-v3'
import { createAdminV4Api } from './api-v4'

type UnauthorizedHandler = (e: ApiError) => void
let onUnauthorized: UnauthorizedHandler = () => undefined
export function setUnauthorizedHandler(fn: UnauthorizedHandler): void {
  onUnauthorized = fn
}

export const client = new ApiClient({
  baseUrl: '/api/v1',
  getLocale: () => currentLocale(),
  onUnauthorized: (e) => onUnauthorized(e),
})
export const api = createAdminApi(client)
/** review-v3 §12 admin endpoints (api-v3.ts). */
export const apiV3 = createAdminV3Api(client)
/** review-v4 §12 admin endpoints (api-v4.ts). */
export const apiV4 = createAdminV4Api(client)

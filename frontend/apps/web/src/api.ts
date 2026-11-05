import { ApiClient, createBuyerApi, type ApiError } from '@24goods/shared'
import { currentLocale } from './i18n'

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
export const api = createBuyerApi(client)

/** Error codes from docs/api-contract.md plus client-side codes (network, server_error, unknown). */
export const API_ERROR_CODES = [
  'buyer_input_disabled', 'challenge_required', 'challenge_invalid', 'payment_attempt_unknown', 'site_visit_required',
  'unauthorized',
  'forbidden',
  'csrf',
  'not_found',
  'validation',
  'rate_limited',
  'conflict',
  'out_of_stock',
  'invalid_state',
  'quote_expired',
  'upload_rejected',
  // v3
  'pow_required',
  'pow_invalid',
  'has_open_orders',
  'invalid_password',
  'after_sales_closed',
  'after_sales_open',
  // v4
  'too_frequent',
  'fee_channel_mismatch',
  'too_many_unpaid',
  'channel_unavailable',
  'buyer_input_too_long',
  'pay_via_checkout',
  'already_paid',
  'buyer_input_required',
  // v5
  'feature_disabled',
  'turnstile_failed',
  'email_code_required',
  'email_code_invalid',
  'terms_required',
  'promo_invalid',
  'promo_not_started',
  'promo_expired',
  'promo_exhausted',
  'promo_user_limit',
  'promo_min_subtotal',
  'promo_not_applicable',
  'reprice_checkout_member',
  'reprice_not_goods',
  'reprice_below_floor',
  'slug_taken',
  'slug_invalid',
  'payout_below_min',
  'payout_exceeds_available',
  'payout_open',
  'oauth_failed',
  'product_in_use',
  'category_in_use',
  'insufficient_balance',
  'invalid_code',
  'payout_below_minimum',
  'payout_account_required',
  'markup_too_high',
  'slug_reserved',
  'reseller_exists',
  'fetch_failed',
  'unsafe_url',
  'unavailable',
  // v5.2
  'account_suspended',
  'ip_blocked',
  'custom_domains_unavailable',
  'domain_limit',
  'domain_taken',
  'bad_hostname',
  'hostname_reserved',
  'domain_provider_error',
  'domain_no_cf_id',
] as const
export const CLIENT_ERROR_CODES = ['network', 'server_error', 'timeout', 'unknown'] as const
export type ContractErrorCode = (typeof API_ERROR_CODES)[number]
export type ClientErrorCode = (typeof CLIENT_ERROR_CODES)[number]
export type ApiErrorCode = ContractErrorCode | ClientErrorCode

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  /** Raw `error` object or body, for diagnostics only. Never shown to users. */
  readonly details: unknown

  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

export function isKnownErrorCode(code: string): code is ApiErrorCode {
  return (
    (API_ERROR_CODES as readonly string[]).includes(code) ||
    (CLIENT_ERROR_CODES as readonly string[]).includes(code)
  )
}

/** Fallback code from the HTTP status when the body is not a contract error envelope. */
export function codeFromStatus(status: number): ApiErrorCode {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 400 || status === 422) return 'validation'
  if (status === 413) return 'upload_rejected'
  if (status === 429) return 'rate_limited'
  if (status >= 500) return 'server_error'
  return 'unknown'
}

const CODE_RE = /^[a-z][a-z0-9_]{0,63}$/

/**
 * Map a non-2xx response body to an ApiError.
 * Contract shape: `{ "error": { "code": "snake_case", "message": "..." } }`.
 * Also tolerates FastAPI's default `{ "detail": ... }` (→ validation / status code).
 */
export function errorFromBody(status: number, body: unknown): ApiError {
  // v4 global access-frequency limiter: fixed body {"code":429,"msg":"..."} (not the error envelope).
  if (status === 429 && body && typeof body === 'object' && (body as { code?: unknown }).code === 429) {
    return new ApiError('too_frequent', 'Access is too frequent', 429, body)
  }
  if (body && typeof body === 'object' && 'error' in body) {
    const err = (body as { error: unknown }).error
    if (err && typeof err === 'object') {
      const code = (err as { code?: unknown }).code
      const message = (err as { message?: unknown }).message
      if (typeof code === 'string' && CODE_RE.test(code)) {
        return new ApiError(code, typeof message === 'string' ? message : code, status, err)
      }
    }
  }
  const code = codeFromStatus(status)
  return new ApiError(code, `HTTP ${status}`, status, body)
}

/** The extra `details` payload of an error envelope (e.g. `{checkout_no}`, `{product_id}`), if any. */
export function errorExtra(e: unknown): Record<string, unknown> {
  if (!isApiError(e) || !e.details || typeof e.details !== 'object') return {}
  const d = e.details as Record<string, unknown>
  const nested = d.details && typeof d.details === 'object' ? (d.details as Record<string, unknown>) : {}
  return { ...nested, ...d }
}

/** i18n key for an error (falls back to errors.unknown). Use with the shared bundle. */
export function errorI18nKey(e: unknown): string {
  if (isApiError(e)) return isKnownErrorCode(e.code) ? `errors.${e.code}` : 'errors.unknown'
  return 'errors.unknown'
}

/**
 * The server's field detail for a 422, e.g. `secret: String should have at least 16 characters`.
 *
 * Only for `validation`: that message is built by the API's RequestValidationError handler as
 * `<field>: <constraint>` and names nothing a caller cannot already see. Other codes carry prose
 * meant for logs, so they stay behind their translated key rather than reaching a toast.
 *
 * Returns undefined when there is no detail worth adding, so a caller can fall back to the
 * translated message alone. The text itself is English (it comes from pydantic) and is meant to be
 * shown *beside* the localized message, never instead of it.
 */
export function validationDetail(e: unknown): string | undefined {
  if (!isApiError(e) || e.code !== 'validation') return undefined
  const msg = e.message.trim()
  // The handler sends "Invalid request" when it cannot name a field: nothing to add.
  // The handler formats exactly `${loc}: ${msg}` -- colon AND space. Splitting on a bare ':' would
  // take "https" from "https://evil.example/x: ..." as the field and pass the check below.
  const sep = msg.indexOf(': ')
  if (!msg || msg === 'Invalid request' || sep < 1) return undefined
  // pydantic's `loc` can carry a client-chosen key (a dict body key, a bad enum value), so only a
  // plain dotted field path is trusted as the prefix. Anything else could put attacker-written
  // prose in a staff toast; it renders as text, but it is still words we did not write.
  const field = msg.slice(0, sep)
  if (!/^[A-Za-z_][\w.[\]-]{0,63}$/.test(field)) return undefined
  return msg.slice(0, 160)
}

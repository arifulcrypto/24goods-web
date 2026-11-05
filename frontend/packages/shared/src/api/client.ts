import { ApiError, errorFromBody } from './errors'

export type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOptions {
  query?: Query
  /** JSON body (serialized). */
  body?: unknown
  /** multipart body (takes precedence over `body`). */
  form?: FormData
  signal?: AbortSignal
  /** Do not invoke the global onUnauthorized hook for a 401 (e.g. login, /auth/me probe). */
  skipAuthHandler?: boolean
}

export interface ApiClientOptions {
  /** Default `/api/v1`. Same origin only. */
  baseUrl?: string
  /** Current UI locale; sent as Accept-Language. */
  getLocale?: () => string
  /** Called on any 401 unless the request opted out. */
  onUnauthorized?: (err: ApiError) => void
  fetchImpl?: typeof fetch
}

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export function buildQuery(query?: Query): string {
  if (!query) return ''
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    sp.append(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export class ApiClient {
  readonly baseUrl: string
  private readonly opts: ApiClientOptions

  constructor(opts: ApiClientOptions = {}) {
    this.opts = opts
    this.baseUrl = (opts.baseUrl ?? '/api/v1').replace(/\/+$/, '')
  }

  url(path: string, query?: Query): string {
    if (!path.startsWith('/')) throw new Error('path must start with /')
    return `${this.baseUrl}${path}${buildQuery(query)}`
  }

  async request<T>(method: HttpMethod, path: string, o: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      // CSRF: the API rejects non-GET requests without this header (403 csrf). Always send it.
      'X-Requested-With': 'fetch',
    }
    const locale = this.opts.getLocale?.()
    if (locale) headers['Accept-Language'] = locale
    let body: BodyInit | undefined
    if (o.form) {
      body = o.form // browser sets multipart boundary
    } else if (o.body !== undefined) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(o.body)
    }
    const doFetch = this.opts.fetchImpl ?? globalThis.fetch.bind(globalThis)
    let res: Response
    try {
      res = await doFetch(this.url(path, o.query), {
        method,
        headers,
        body,
        credentials: 'same-origin',
        signal: o.signal,
        redirect: 'follow',
        cache: 'no-store',
      })
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') throw e
      throw new ApiError('network', 'Network error', 0, e)
    }
    if (res.status === 204) return undefined as T
    const text = await res.text().catch(() => '')
    let parsed: unknown = undefined
    if (text) {
      try {
        parsed = JSON.parse(text)
      } catch {
        parsed = text
      }
    }
    if (!res.ok) {
      const err = errorFromBody(res.status, parsed)
      if (res.status === 401 && !o.skipAuthHandler) this.opts.onUnauthorized?.(err)
      throw err
    }
    if (typeof parsed === 'string') {
      throw new ApiError('server_error', 'Invalid JSON response', res.status, parsed)
    }
    return parsed as T
  }

  get<T>(path: string, query?: Query, o: Omit<RequestOptions, 'query' | 'body' | 'form'> = {}) {
    return this.request<T>('GET', path, { ...o, query })
  }
  post<T>(path: string, body?: unknown, o: Omit<RequestOptions, 'body'> = {}) {
    return this.request<T>('POST', path, { ...o, body })
  }
  put<T>(path: string, body?: unknown, o: Omit<RequestOptions, 'body'> = {}) {
    return this.request<T>('PUT', path, { ...o, body })
  }
  patch<T>(path: string, body?: unknown, o: Omit<RequestOptions, 'body'> = {}) {
    return this.request<T>('PATCH', path, { ...o, body })
  }
  delete<T>(path: string, o: RequestOptions = {}) {
    return this.request<T>('DELETE', path, o)
  }
  upload<T>(path: string, form: FormData, o: Omit<RequestOptions, 'form' | 'body'> = {}) {
    return this.request<T>('POST', path, { ...o, form })
  }
}

/** Encode a path segment (ids are UUIDs, but never trust them blindly). */
export const seg = (v: string | number): string => encodeURIComponent(String(v))

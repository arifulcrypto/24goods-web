import { describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiError, buildQuery, errorFromBody, errorI18nKey, safeAssetUrl, safePayUrl, safeRedirect, validationDetail } from '../src'

function res(status: number, body?: unknown, raw?: string): Response {
  const text = raw ?? (body === undefined ? '' : JSON.stringify(body))
  return new Response(status === 204 ? null : text, { status, headers: { 'Content-Type': 'application/json' } })
}

describe('ApiClient', () => {
  it('sends CSRF header, same-origin credentials, JSON body and Accept-Language', async () => {
    const fetchImpl = vi.fn(async () => res(200, { ok: true }))
    const c = new ApiClient({ fetchImpl, getLocale: () => 'zh-CN' })
    await c.post('/orders', { product_id: 'p', qty: 1 })
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/v1/orders')
    const h = init.headers as Record<string, string>
    expect(h['X-Requested-With']).toBe('fetch')
    expect(h['Accept-Language']).toBe('zh-CN')
    expect(h['Content-Type']).toBe('application/json')
    expect(init.credentials).toBe('same-origin')
    expect(init.body).toBe('{"product_id":"p","qty":1}')
  })

  it('sends the CSRF header on GET too and drops empty query params', async () => {
    const fetchImpl = vi.fn(async () => res(200, []))
    const c = new ApiClient({ fetchImpl })
    await c.get('/products', { q: 'steam', category: '', page: 2, x: undefined })
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/v1/products?q=steam&page=2')
    expect((init.headers as Record<string, string>)['X-Requested-With']).toBe('fetch')
  })

  it('maps the contract error envelope to ApiError{code,message,status}', async () => {
    const c = new ApiClient({ fetchImpl: async () => res(409, { error: { code: 'out_of_stock', message: 'Out of stock' } }) })
    const err = await c.post('/orders', {}).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ code: 'out_of_stock', message: 'Out of stock', status: 409 })
    expect(errorI18nKey(err)).toBe('errors.out_of_stock')
  })

  it('falls back to status-derived codes for non-envelope bodies', () => {
    expect(errorFromBody(422, { detail: [{ loc: ['body'] }] }).code).toBe('validation')
    expect(errorFromBody(429, 'Too Many').code).toBe('rate_limited')
    expect(errorFromBody(502, '<html>').code).toBe('server_error')
    expect(errorFromBody(404, undefined).code).toBe('not_found')
    // malformed code is not trusted
    expect(errorFromBody(400, { error: { code: '<script>', message: 'x' } }).code).toBe('validation')
  })

  it('unknown codes translate to errors.unknown', () => {
    expect(errorI18nKey(new ApiError('brand_new_code', 'x', 400))).toBe('errors.unknown')
    expect(errorI18nKey(new Error('x'))).toBe('errors.unknown')
  })

  it('maps fetch failures to code=network', async () => {
    const c = new ApiClient({
      fetchImpl: async () => {
        throw new TypeError('Failed to fetch')
      },
    })
    await expect(c.get('/products')).rejects.toMatchObject({ code: 'network', status: 0 })
  })

  it('invokes onUnauthorized for 401 unless skipped', async () => {
    const onUnauthorized = vi.fn()
    const c = new ApiClient({
      onUnauthorized,
      fetchImpl: async () => res(401, { error: { code: 'unauthorized', message: 'no' } }),
    })
    await expect(c.get('/orders')).rejects.toMatchObject({ code: 'unauthorized', status: 401 })
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    await expect(c.get('/auth/me', undefined, { skipAuthHandler: true })).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('returns undefined for 204 and rejects non-JSON success bodies', async () => {
    const c1 = new ApiClient({ fetchImpl: async () => res(204) })
    await expect(c1.post('/auth/logout')).resolves.toBeUndefined()
    const c2 = new ApiClient({ fetchImpl: async () => res(200, undefined, '<html>') })
    await expect(c2.get('/x')).rejects.toMatchObject({ code: 'server_error' })
  })

  it('does not set Content-Type for multipart uploads', async () => {
    const fetchImpl = vi.fn(async () => res(200, { id: 'a' }))
    const c = new ApiClient({ fetchImpl })
    const fd = new FormData()
    fd.append('kind', 'image')
    await c.upload('/uploads', fd)
    const init = (fetchImpl.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined()
    expect(init.body).toBe(fd)
  })

  it('buildQuery encodes values', () => {
    expect(buildQuery({ q: 'a b&c' })).toBe('?q=a+b%26c')
    expect(buildQuery({})).toBe('')
  })
})

describe('URL safety', () => {
  it('safePayUrl allows same-origin or https on allowlisted gateway hosts only', () => {
    const base = 'https://shop.test/orders/1'
    expect(safePayUrl('/mockpay/submit.php?x=1', base)).toBe('https://shop.test/mockpay/submit.php?x=1')
    expect(safePayUrl('http://107.0.0.1/mockpay/submit.php', 'http://107.0.0.1/chat')).toBe('http://107.0.0.1/mockpay/submit.php')
    expect(safePayUrl('https://zpayz.cn/submit.php?pid=1', base)).toBe('https://zpayz.cn/submit.php?pid=1')
    expect(safePayUrl('http://zpayz.cn/submit.php', base)).toBeNull() // no plain http off-origin
    expect(safePayUrl('https://evil.example/submit.php', base)).toBeNull() // not allowlisted
    expect(safePayUrl('https://zpayz.cn.evil.example/x', base)).toBeNull()
    expect(safePayUrl('javascript:alert(1)', base)).toBeNull()
    expect(safePayUrl('data:text/html,x', base)).toBeNull()
    expect(safePayUrl('https://pay.partner/x', base, ['pay.partner'])).toBe('https://pay.partner/x')
  })
  it('safeAssetUrl blocks script/data/blob schemes and plain-http off-origin', () => {
    const base = 'http://107.0.0.1/orders/1'
    expect(safeAssetUrl('/api/v1/files/abc', base)).toBe('http://107.0.0.1/api/v1/files/abc')
    expect(safeAssetUrl('https://media.example/x.webp', base)).toBe('https://media.example/x.webp')
    expect(safeAssetUrl('http://evil.example/x.webp', base)).toBeNull()
    expect(safeAssetUrl('javascript:alert(1)', base)).toBeNull()
    expect(safeAssetUrl('data:image/svg+xml,<svg onload=alert(1)>', base)).toBeNull()
    expect(safeAssetUrl('blob:http://107.0.0.1/uuid', base)).toBeNull()
    expect(safeAssetUrl(null, base)).toBeNull()
  })
  it('safeRedirect only allows internal paths', () => {
    expect(safeRedirect('/orders/1')).toBe('/orders/1')
    expect(safeRedirect('//evil.com')).toBe('/')
    expect(safeRedirect('/\\evil.com')).toBe('/')
    expect(safeRedirect('https://evil.com')).toBe('/')
    expect(safeRedirect(undefined, '/me')).toBe('/me')
  })
})

describe('v4 global 429', () => {
  it('maps {"code":429,"msg"} to too_frequent and fires onRateLimited', async () => {
    const seen: string[] = []
    const c = new ApiClient({
      fetchImpl: (async () =>
        new Response(JSON.stringify({ code: 429, msg: 'Access is too frequent. Please try again later.' }), {
          status: 429,
        })) as typeof fetch,
      onRateLimited: (e) => seen.push(e.code),
    })
    await expect(c.get('/x')).rejects.toMatchObject({ code: 'too_frequent', status: 429 })
    expect(seen).toEqual(['too_frequent'])
  })
})

describe('validationDetail', () => {
  const err = (code: string, message: string) => new ApiError(code, message, 422)

  it('surfaces the field and constraint the API already sent', () => {
    expect(validationDetail(err('validation', 'secret: String should have at least 16 characters')))
      .toBe('secret: String should have at least 16 characters')
  })

  it('stays silent when the handler could not name a field', () => {
    // The API sends exactly this when errors()[0] has no usable loc; appending it to the
    // translated headline would just read "Please check your input — Invalid request".
    expect(validationDetail(err('validation', 'Invalid request'))).toBeUndefined()
    expect(validationDetail(err('validation', ''))).toBeUndefined()
  })

  it('never leaks prose from other error codes', () => {
    // forbidden/conflict/server_error messages are written for logs, not for a toast; they stay
    // behind their translated key.
    for (const code of ['forbidden', 'conflict', 'server_error', 'rate_limited']) {
      expect(validationDetail(err(code, 'internal detail: do not show this'))).toBeUndefined()
    }
  })

  it('ignores non-API errors and caps runaway messages', () => {
    expect(validationDetail(new Error('boom'))).toBeUndefined()
    expect(validationDetail(undefined)).toBeUndefined()
    const long = validationDetail(err('validation', 'field: ' + 'x'.repeat(500)))
    expect(long!.length).toBe(160)
  })

  it('accepts nested and indexed field paths', () => {
    expect(validationDetail(err('validation', 'items.0.qty: Input should be greater than 0'))).toBeDefined()
    expect(validationDetail(err('validation', 'price: invalid decimal'))).toBeDefined()
  })

  it('refuses a prefix that is not a plain field path', () => {
    // pydantic's loc can carry a client-chosen dict key; that must not become staff-facing prose.
    for (const msg of [
      'Your session expired, call +1 555 0100 to restore: x',
      'https://evil.example/reset: Field required',
      '<b>urgent</b>: Field required',
      '9field: Field required',
    ]) {
      expect(validationDetail(err('validation', msg)), msg).toBeUndefined()
    }
  })
})

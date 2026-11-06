/**
 * Cloudflare Turnstile loader (review-v5 §5.1, ADR-022: the ONE documented exception to the
 * "no third-party runtime assets" invariant; Cloudflare forbids self-hosting the script).
 * The script is injected lazily, only by a mounted TurnstileWidget (i.e. only when the site has
 * Turnstile enabled AND the current form's action is protected).
 */
export const TURNSTILE_SCRIPT = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

export interface TurnstileApi {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string
  reset: (id?: string) => void
  remove: (id?: string) => void
}
declare global {
  interface Window {
    turnstile?: TurnstileApi
  }
}

let loading: Promise<TurnstileApi> | null = null

export function loadTurnstile(timeoutMs = 15000): Promise<TurnstileApi> {
  if (globalThis.window?.turnstile) return Promise.resolve(globalThis.window.turnstile)
  if (loading) return loading
  loading = new Promise<TurnstileApi>((resolve, reject) => {
    const s = document.createElement('script')
    s.async = true
    s.defer = true
    s.referrerPolicy = 'no-referrer'
    const timer = setTimeout(() => fail(new Error('turnstile timeout')), timeoutMs)
    function fail(e: Error) {
      clearTimeout(timer)
      s.remove()
      loading = null // allow a retry
      reject(e)
    }
    s.onload = () => {
      clearTimeout(timer)
      if (window.turnstile) resolve(window.turnstile)
      else fail(new Error('turnstile missing'))
    }
    s.onerror = () => fail(new Error('turnstile load failed'))
    s.src = TURNSTILE_SCRIPT
    document.head.appendChild(s)
  })
  return loading
}

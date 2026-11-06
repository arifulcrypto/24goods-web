import { inject, type InjectionKey } from 'vue'

/**
 * UI-library-neutral hooks for SmartText (chips). Each app provides its own dialog/toast
 * (Vant in the buyer app, Element Plus in admin) via `app.provide(SMART_TEXT_KEY, adapter)`.
 * Without a provider the defaults use `window.confirm` and a no-op toast.
 */
export interface SmartTextAdapter {
  /** Ask the user to confirm opening an external link; show the FULL url. Resolve true to open. */
  confirmOpenUrl(url: string): Promise<boolean>
  toast(message: string): void
}

export const SMART_TEXT_KEY: InjectionKey<SmartTextAdapter> = Symbol('smart-text')

export const defaultSmartTextAdapter: SmartTextAdapter = {
  confirmOpenUrl: (url) => Promise.resolve(typeof window !== 'undefined' && window.confirm(url)),
  toast: () => undefined,
}

export function useSmartTextAdapter(): SmartTextAdapter {
  return inject(SMART_TEXT_KEY, defaultSmartTextAdapter)
}

/** Open an already-confirmed http(s) URL without leaking opener/referrer. */
export function openExternal(url: string): void {
  if (!/^https?:\/\//i.test(url)) return
  window.open(url, '_blank', 'noopener,noreferrer')
}

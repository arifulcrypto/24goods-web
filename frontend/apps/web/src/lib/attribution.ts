/** Traffic attribution (`src` of a direct buy link) and one-time order secrets (access codes). */
const SRC_KEY = 'kesiv.src'
const CODE_PREFIX = 'kesiv.ac.'

export function cleanSrc(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const v = raw.trim().toLowerCase()
  return /^[a-z0-9_-]{1,32}$/.test(v) ? v : null
}
export function rememberSrc(src: string | null): void {
  try {
    if (src) sessionStorage.setItem(SRC_KEY, src)
  } catch {
    /* ignore */
  }
}
export function recalledSrc(): string | null {
  try {
    return cleanSrc(sessionStorage.getItem(SRC_KEY))
  } catch {
    return null
  }
}

/** The access code is returned once by POST /orders; keep it for this tab only. */
export function stashAccessCode(orderId: string, code: string | null | undefined): void {
  if (!code) return
  try {
    sessionStorage.setItem(CODE_PREFIX + orderId, code)
  } catch {
    /* ignore */
  }
}
export function readAccessCode(orderId: string): string | null {
  try {
    return sessionStorage.getItem(CODE_PREFIX + orderId)
  } catch {
    return null
  }
}
export function dropAccessCode(orderId: string): void {
  try {
    sessionStorage.removeItem(CODE_PREFIX + orderId)
  } catch {
    /* ignore */
  }
}

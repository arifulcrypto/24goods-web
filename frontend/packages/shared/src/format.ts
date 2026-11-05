/**
 * Money and date formatting via Intl. Money is a decimal *string* (contract) — we never do
 * arithmetic in floats: helpers convert to integer minor units (bigint) for comparisons.
 */

const MONEY_RE = /^(-)?(\d+)(?:\.(\d{1,2}))?$/

/** "19.9" → 1990n; returns null for anything that is not a plain decimal with ≤2 fraction digits. */
export function toMinor(amount: string): bigint | null {
  const m = MONEY_RE.exec(amount.trim())
  if (!m) return null
  const sign = m[1] ? -1n : 1n
  const whole = BigInt(m[2] ?? '0')
  const frac = BigInt((m[3] ?? '').padEnd(2, '0') || '0')
  return sign * (whole * 100n + frac)
}

/** 1990n → "19.90" */
export function fromMinor(minor: bigint): string {
  const neg = minor < 0n
  const abs = neg ? -minor : minor
  const whole = abs / 100n
  const frac = (abs % 100n).toString().padStart(2, '0')
  return `${neg ? '-' : ''}${whole.toString()}.${frac}`
}

/** True for a positive amount like "1", "1.5", "19.90" (max 9 integer digits). */
export function isValidAmount(amount: string): boolean {
  const s = amount.trim()
  if (!/^\d{1,9}(\.\d{1,2})?$/.test(s)) return false
  const m = toMinor(s)
  return m !== null && m > 0n
}

/** Normalize user input to the contract's 2-decimal string ("19.9" → "19.90"). */
export function normalizeAmount(amount: string): string | null {
  const m = toMinor(amount)
  return m === null ? null : fromMinor(m)
}

/** a × qty, as a 2-decimal string (exact). */
export function multiplyAmount(amount: string, qty: number): string | null {
  const m = toMinor(amount)
  if (m === null || !Number.isInteger(qty)) return null
  return fromMinor(m * BigInt(qty))
}

/** Compare two money strings: -1, 0, 1 (null if either is invalid). */
export function compareAmount(a: string, b: string): -1 | 0 | 1 | null {
  const x = toMinor(a)
  const y = toMinor(b)
  if (x === null || y === null) return null
  return x < y ? -1 : x > y ? 1 : 0
}

const nfCache = new Map<string, Intl.NumberFormat>()
function moneyFormatter(locale: string, currency: string): Intl.NumberFormat {
  const key = `${locale}|${currency}`
  let f = nfCache.get(key)
  if (!f) {
    try {
      f = new Intl.NumberFormat(locale, {
        style: 'currency',
        currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    } catch {
      f = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    }
    nfCache.set(key, f)
  }
  return f
}

/**
 * Format a contract money string for display, e.g. ("19.90","CNY","en") → "CN¥19.90",
 * ("19.90","CNY","zh-CN") → "¥19.90". Uses Intl's exact-decimal string input where supported.
 */
export function formatMoney(amount: string | null | undefined, currency: string, locale: string): string {
  if (amount === null || amount === undefined) return '—'
  const norm = normalizeAmount(amount)
  if (norm === null) return amount
  const f = moneyFormatter(locale, currency)
  // ES2023 Intl.NumberFormat v3 accepts decimal strings exactly; TS lib types only allow number.
  return f.format(norm as unknown as number)
}

/** Split for price displays with a small currency symbol: { symbol: "¥", value: "19.90" }. */
export function moneyParts(amount: string, currency: string, locale: string): { symbol: string; value: string } {
  const norm = normalizeAmount(amount) ?? amount
  const parts = moneyFormatter(locale, currency).formatToParts(norm as unknown as number)
  const symbol = parts.filter((p) => p.type === 'currency').map((p) => p.value).join('')
  const value = parts
    .filter((p) => p.type !== 'currency' && p.type !== 'literal')
    .map((p) => p.value)
    .join('')
  return { symbol, value }
}

export function formatNumber(n: number, locale: string): string {
  return new Intl.NumberFormat(locale).format(n)
}

function toDate(iso: string | Date): Date {
  return iso instanceof Date ? iso : new Date(iso)
}

export function formatDateTime(iso: string | null | undefined, locale: string): string {
  if (!iso) return '—'
  const d = toDate(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d)
}

/** Chat-style time: today → "14:05", this year → "11/03 14:05", else full date. */
export function formatChatTime(iso: string, locale: string, now: Date = new Date()): string {
  const d = toDate(iso)
  if (Number.isNaN(d.getTime())) return ''
  const sameDay = d.toDateString() === now.toDateString()
  const opts: Intl.DateTimeFormatOptions = sameDay
    ? { hour: '2-digit', minute: '2-digit' }
    : d.getFullYear() === now.getFullYear()
      ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
      : { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
  return new Intl.DateTimeFormat(locale, opts).format(d)
}

/** Relative time ("3 minutes ago" / "3分钟前"), via Intl.RelativeTimeFormat. */
export function formatRelative(iso: string, locale: string, now: number = Date.now()): string {
  const t = toDate(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Math.round((t - now) / 1000)
  const abs = Math.abs(diff)
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (abs < 45) return rtf.format(0, 'second')
  if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour')
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day')
  return formatDateTime(iso, locale)
}

/** Voice durations: 7000 → "7″", 75000 → "1′15″" (WeChat style). */
export function formatVoiceDuration(ms: number): string {
  const s = Math.max(1, Math.round(ms / 1000))
  if (s < 60) return `${s}″`
  return `${Math.floor(s / 60)}′${String(s % 60).padStart(2, '0')}″`
}

/** Countdown "mm:ss" or "h:mm:ss"; returns null when expired. */
export function formatCountdown(untilIso: string, now: number = Date.now()): string | null {
  const ms = toDate(untilIso).getTime() - now
  if (Number.isNaN(ms) || ms <= 0) return null
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export function formatBytes(n: number, locale: string): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: i === 0 ? 0 : 1 }).format(v)} ${units[i]}`
}

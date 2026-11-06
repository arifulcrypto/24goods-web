/** Long chat text → .txt (review-v3 §6). */
export const LONG_TEXT_ASK = 500
export const LONG_TEXT_FORCE = 2000

export type LongTextDecision = 'text' | 'ask' | 'file'

export function longTextDecision(text: string): LongTextDecision {
  const n = [...text].length
  if (n > LONG_TEXT_FORCE) return 'file'
  if (n > LONG_TEXT_ASK) return 'ask'
  return 'text'
}

/** message-YYYYMMDD-HHmm.txt (local time). */
export function messageFilename(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `message-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.txt`
}

/** Deterministic generated cover for products without an image (review-v3 §2.2). */

const PALETTE: ReadonlyArray<readonly [string, string]> = [
  ['#2f6bff', '#6aa1ff'],
  ['#00a37a', '#4fd1a5'],
  ['#d93f00', '#ff8a4c'],
  ['#7a3cff', '#b18cff'],
  ['#c2185b', '#f06292'],
  ['#00838f', '#4dd0e1'],
  ['#5d6d00', '#a4b82a'],
  ['#37474f', '#78909c'],
]

/** FNV-1a 32-bit. */
export function hash32(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

const CJK = /[㐀-鿿豈-﫿]/u
const BRACKETS = /[【\[(（][^】\])）]*[】\])）]/gu

/** 1 CJK char, or up to 2 Latin initials. Bracket tags like 【优质】 are skipped. */
export function coverInitials(title: string): string {
  const clean = title.replace(BRACKETS, ' ').trim() || title.trim()
  const chars = Array.from(clean)
  const cjk = chars.find((c) => CJK.test(c))
  const words = clean.split(/[\s\-_/|·,.:]+/u).filter((w) => /[\p{L}\p{N}]/u.test(w))
  if (cjk && (!words.length || CJK.test(Array.from(words[0])[0] ?? ''))) return cjk
  const ini = words
    .slice(0, 2)
    .map((w) => Array.from(w).find((c) => /[\p{L}\p{N}]/u.test(c)) ?? '')
    .join('')
  return (ini || chars[0] || '?').toUpperCase()
}

export function coverColors(seed: string): readonly [string, string] {
  return PALETTE[hash32(seed) % PALETTE.length]!
}

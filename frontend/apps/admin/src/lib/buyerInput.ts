import type { BuyerInputForm } from '../api-v4'

export type Chip = { kind: 'text' | 'code'; value: string }

/** Local preview tokenizer for the popup prompt: `backtick` segments become copy chips (§4/§7). */
export function promptChips(prompt: string): Chip[] {
  const out: Chip[] = []
  const re = /`([^`\n]+)`/g
  let last = 0
  for (let m = re.exec(prompt); m; m = re.exec(prompt)) {
    if (m.index > last) out.push({ kind: 'text', value: prompt.slice(last, m.index) })
    out.push({ kind: 'code', value: m[1]! })
    last = m.index + m[0].length
  }
  if (last < prompt.length) out.push({ kind: 'text', value: prompt.slice(last) })
  return out
}

export function validateBuyerInput(b: BuyerInputForm): 'max_len' | 'text_len' | 'title_required' | null {
  if (!Number.isInteger(b.max_len) || b.max_len < 1 || b.max_len > 4000) return 'max_len'
  for (const o of [b.title_i18n, b.prompt_i18n, b.placeholder_i18n]) {
    if ((o.en?.length ?? 0) > 1000 || (o['zh-CN']?.length ?? 0) > 1000) return 'text_len'
  }
  return null
}

/** Rate-limit / surcharge helpers (§1, §10). */
export const permilleToPercent = (p: number): number => Math.round(p) / 10
export const percentToPermille = (pct: number): number => Math.round(pct * 10)
export function cleanQuickQuestions(list: string[]): string[] {
  return list.map((s) => s.trim()).filter(Boolean)
}

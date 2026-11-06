export const TEMPLATE_NAMES = ['verify_code', 'password_reset', 'order_paid', 'order_delivered'] as const
export type TemplateName = (typeof TEMPLATE_NAMES)[number]
export const PLACEHOLDERS = ['{site_name}', '{code}', '{minutes}', '{order_no}', '{order_url}', '{title}', '{amount}'] as const

/** Replace the selection [s, e) with a placeholder; returns new text and caret position. */
export function insertPlaceholder(text: string, s: number, e: number, ph: string): { text: string; caret: number } {
  const a = Math.max(0, Math.min(s, text.length))
  const b = Math.max(a, Math.min(e, text.length))
  return { text: text.slice(0, a) + ph + text.slice(b), caret: a + ph.length }
}

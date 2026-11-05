/** Text helpers for the order drawer (review-v3 §6 "Copy as text"). Pure, unit-tested. */

/** Plain text of an HTML fragment (sanitized server-side, but we only ever take textContent). */
export function htmlToText(html: string | null | undefined): string {
  if (!html) return ''
  if (typeof DOMParser === 'undefined') return html.replace(/<[^>]*>/g, '')
  const doc = new DOMParser().parseFromString(html.replace(/<\/(p|div|li|h\d)>|<br\s*\/?>/gi, '\n'), 'text/html')
  return (doc.body.textContent ?? '').replace(/\n{3,}/g, '\n\n').trim()
}

export interface BuyerTemplateInput {
  orderNo: string
  title: string
  content: string
  usage?: string | null
  redeemUrl?: string | null
  afterSalesUntil?: string | null
  /** localised labels + date formatter */
  labels: { order: string; item: string; content: string; usage: string; redeem: string; afterSales: string; thanks: string }
  formatDate: (iso: string) => string
}

/** A message staff can paste to the buyer on any channel. Empty sections are omitted. */
export function buildBuyerTemplate(i: BuyerTemplateInput): string {
  const out = [`${i.labels.order}: ${i.orderNo}`, `${i.labels.item}: ${i.title}`, '', `${i.labels.content}:`, i.content.trim()]
  if (i.redeemUrl) out.push('', `${i.labels.redeem}: ${i.redeemUrl}`)
  if (i.usage?.trim()) out.push('', `${i.labels.usage}:`, i.usage.trim())
  if (i.afterSalesUntil) out.push('', `${i.labels.afterSales}: ${i.formatDate(i.afterSalesUntil)}`)
  out.push('', i.labels.thanks)
  return out.join('\n')
}

/** "14:05" style clock + whether a stamp is today, for "Delivered 14:05 · 3 min ago". */
export function clockTime(iso: string, locale: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(d)
}

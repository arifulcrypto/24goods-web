import type { Message, QuoteCardPayload } from '../types'

/**
 * Upsert `incoming` into an ascending-by-id list. Same id → replaced (lets the server re-emit a
 * card with an updated payload, e.g. quote status). Returns a new array, sorted ascending.
 */
export function mergeMessages(list: readonly Message[], incoming: readonly Message[]): Message[] {
  if (incoming.length === 0) return list as Message[]
  const byId = new Map<number, Message>()
  for (const m of list) byId.set(m.id, m)
  for (const m of incoming) byId.set(m.id, m)
  return Array.from(byId.values()).sort((a, b) => a.id - b.id)
}

/** Latest known payload per quote id across a message list (later messages win). */
export function latestQuotes(list: readonly Message[]): QuoteCardPayload[] {
  const map = new Map<string, QuoteCardPayload>()
  for (const m of list) {
    if (m.type === 'quote_card' && m.payload) {
      const q = m.payload as QuoteCardPayload
      if (typeof q.quote_id === 'string') map.set(q.quote_id, q)
    }
  }
  return Array.from(map.values()).reverse()
}

/** Effective quote status: an `offered` quote past expires_at is shown as expired. */
export function effectiveQuoteStatus(q: QuoteCardPayload, now: number = Date.now()): QuoteCardPayload['status'] {
  if (q.status === 'offered' && new Date(q.expires_at).getTime() <= now) return 'expired'
  return q.status
}

/** Show a time divider between messages more than `gapMs` apart. */
export function needsTimeDivider(prev: { created_at: string } | undefined, cur: { created_at: string }, gapMs = 5 * 60_000): boolean {
  if (!prev) return true
  return new Date(cur.created_at).getTime() - new Date(prev.created_at).getTime() > gapMs
}

type Translate = (key: string, params?: Record<string, unknown>) => string

/** One-line preview of a message for conversation lists. */
export function messagePreview(m: Message | null | undefined, t: Translate, max = 60): string {
  if (!m) return ''
  if (m.payload && (m.payload as { redacted?: boolean }).redacted === true && m.type !== 'system') {
    return t('chat.redacted')
  }
  if (m.type === 'text') {
    const s = (m.text ?? '').replace(/\s+/g, ' ').trim()
    return s.length > max ? `${s.slice(0, max)}…` : s
  }
  if (m.type === 'system' && m.text) return m.text.length > max ? `${m.text.slice(0, max)}…` : m.text
  return t(`chat.preview.${m.type}`)
}

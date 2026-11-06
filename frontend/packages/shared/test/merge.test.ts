import { describe, expect, it } from 'vitest'
import { effectiveQuoteStatus, latestQuotes, mergeMessages, messagePreview, needsTimeDivider } from '../src'
import type { Message, QuoteCardPayload } from '../src'

const msg = (id: number, extra: Partial<Message> = {}): Message => ({
  id,
  conversation_id: 'c',
  client_msg_id: null,
  sender: { id: 'u', role: 'buyer', display_name: 'B' },
  type: 'text',
  text: `m${id}`,
  attachment: null,
  payload: null,
  created_at: '2026-01-01T00:00:00Z',
  ...extra,
})

describe('chat merge', () => {
  it('dedupes by id, sorts ascending and lets later copies win', () => {
    const out = mergeMessages([msg(3), msg(1)], [msg(2), msg(3, { text: 'updated' })])
    expect(out.map((m) => m.id)).toEqual([1, 2, 3])
    expect(out[2]!.text).toBe('updated')
  })
  it('tracks latest quote payload and effective expiry', () => {
    const q = (status: QuoteCardPayload['status']): QuoteCardPayload => ({
      quote_id: 'q1', title: 't', amount: '1.00', currency: 'CNY', qty: 1, note: null, status,
      expires_at: '2026-01-01T00:10:00Z', order_id: null,
    })
    const list = [msg(1, { type: 'quote_card', payload: q('offered') }), msg(2, { type: 'quote_card', payload: q('withdrawn') })]
    expect(latestQuotes(list)).toHaveLength(1)
    expect(latestQuotes(list)[0]!.status).toBe('withdrawn')
    expect(effectiveQuoteStatus(q('offered'), Date.parse('2026-01-01T00:11:00Z'))).toBe('expired')
    expect(effectiveQuoteStatus(q('offered'), Date.parse('2026-01-01T00:09:00Z'))).toBe('offered')
  })
  it('time dividers after 5 minute gaps', () => {
    expect(needsTimeDivider(undefined, { created_at: '2026-01-01T00:00:00Z' })).toBe(true)
    expect(needsTimeDivider({ created_at: '2026-01-01T00:00:00Z' }, { created_at: '2026-01-01T00:04:00Z' })).toBe(false)
    expect(needsTimeDivider({ created_at: '2026-01-01T00:00:00Z' }, { created_at: '2026-01-01T00:06:00Z' })).toBe(true)
  })
})

describe('redacted messages', () => {
  it('previews as "Message deleted" instead of the (removed) content', () => {
    const t = (k: string) => k
    const m = msg(5, { text: null, payload: { redacted: true } })
    expect(messagePreview(m, t)).toBe('chat.redacted')
  })
})

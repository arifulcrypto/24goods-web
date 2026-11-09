import { describe, expect, it } from 'vitest'
import { isExpediteMessage, isUrgentPaid } from '../src/lib/notifyPolicy'

describe('urgent desk alerts (v9 §6)', () => {
  it('only a paid manual goods order is urgent', () => {
    expect(isUrgentPaid('paid', { delivery_mode: 'manual', kind: 'goods' })).toBe(true)
    expect(isUrgentPaid('paid', { delivery_mode: 'auto_card', kind: 'goods' })).toBe(false)
    expect(isUrgentPaid('created', { delivery_mode: 'manual', kind: 'goods' })).toBe(false)
    expect(isUrgentPaid(null, { delivery_mode: 'manual' })).toBe(false)
  })
  it('recognises the buyer reminder system message', () => {
    expect(isExpediteMessage({ type: 'system', payload: { event: 'delivery_expedite' } })).toBe(true)
    expect(isExpediteMessage({ type: 'system', payload: { event: 'paid' } })).toBe(false)
    expect(isExpediteMessage({ type: 'text', payload: { event: 'delivery_expedite' } })).toBe(false)
  })
})

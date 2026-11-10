import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TYPING_PING_MS, TYPING_SHOW_MS, useTyping } from '../src/chat/useTyping'
import { titleWithCount } from '../src/notify'

describe('useTyping', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  function make() {
    const send = vi.fn(async () => undefined)
    const t = useTyping({ send, conversationId: () => 'c1', otherRole: 'agent' })
    return { send, t }
  }
  const ev = (role: 'buyer' | 'agent', cid = 'c1') => ({ conversation_id: cid, role, name: 'Ann', until: '' })

  it('shows the other side for 6 s and ignores own role / other conversations', () => {
    const { t } = make()
    t.onEvent(ev('buyer'))
    t.onEvent(ev('agent', 'c2'))
    expect(t.name.value).toBeNull()
    t.onEvent(ev('agent'))
    expect(t.name.value).toBe('Ann')
    vi.advanceTimersByTime(TYPING_SHOW_MS + 1)
    expect(t.name.value).toBeNull()
  })

  it('hides when a message from that side arrives', () => {
    const { t } = make()
    t.onEvent(ev('agent'))
    t.clearFor('buyer', 'c1')
    expect(t.name.value).toBe('Ann')
    t.clearFor('agent', 'c1')
    expect(t.name.value).toBeNull()
  })

  it('throttles own pings to one per 3 s', () => {
    const { t, send } = make()
    t.ping(10_000)
    t.ping(10_000 + TYPING_PING_MS - 1)
    expect(send).toHaveBeenCalledTimes(1)
    t.ping(10_000 + TYPING_PING_MS)
    expect(send).toHaveBeenCalledTimes(2)
  })
})

describe('titleWithCount', () => {
  it('prefixes the unread count and caps at 99+', () => {
    expect(titleWithCount('Shop', 0)).toBe('Shop')
    expect(titleWithCount('Shop', 3)).toBe('(3) Shop')
    expect(titleWithCount('Shop', 120)).toBe('(99+) Shop')
  })
})

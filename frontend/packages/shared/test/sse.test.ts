import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RealtimeClient, backoffDelay, type EventSourceLike, type RealtimeState } from '../src'

class FakeES implements EventSourceLike {
  static instances: FakeES[] = []
  onopen: ((ev: Event) => unknown) | null = null
  onerror: ((ev: Event) => unknown) | null = null
  closed = false
  listeners = new Map<string, (ev: MessageEvent) => void>()
  constructor(readonly url: string) {
    FakeES.instances.push(this)
  }
  addEventListener(type: string, l: (ev: MessageEvent) => void) {
    this.listeners.set(type, l)
  }
  close() {
    this.closed = true
  }
  open() {
    this.onopen?.(new Event('open'))
  }
  fail() {
    this.onerror?.(new Event('error'))
  }
  emit(type: string, data: unknown) {
    this.listeners.get(type)?.({ data: typeof data === 'string' ? data : JSON.stringify(data) } as MessageEvent)
  }
}
const last = () => FakeES.instances[FakeES.instances.length - 1]!

describe('RealtimeClient', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeES.instances = []
  })
  afterEach(() => vi.useRealTimers())

  function make(extra: Partial<ConstructorParameters<typeof RealtimeClient>[0]['handlers']> = {}) {
    const states: RealtimeState[] = []
    const poll = vi.fn()
    const resync = vi.fn()
    const message = vi.fn()
    const order = vi.fn()
    const client = new RealtimeClient({
      url: '/api/v1/stream',
      eventSourceFactory: (u) => new FakeES(u),
      random: () => 1, // no jitter → nominal delays
      handlers: { poll, resync, message, order, state: (s) => states.push(s), ...extra },
    })
    return { client, states, poll, resync, message, order }
  }

  it('dispatches typed events and ignores malformed data', () => {
    const { client, message, order } = make()
    client.start()
    last().open()
    last().emit('message', { conversation_id: 'c1', message: { id: 1 } })
    last().emit('order', { id: 'o1', status: 'paid' })
    last().emit('message', 'not json{')
    expect(message).toHaveBeenCalledTimes(1)
    expect(message.mock.calls[0]![0]).toMatchObject({ conversation_id: 'c1' })
    expect(order).toHaveBeenCalledWith({ id: 'o1', status: 'paid' })
    client.stop()
  })

  it('reconnects with exponential backoff and falls back to polling every 4 s', () => {
    const { client, poll, states } = make()
    client.start()
    expect(FakeES.instances).toHaveLength(1)
    last().fail() // failure 1 → reconnect in 1 s, no polling yet
    expect(last().closed).toBe(true)
    expect(poll).not.toHaveBeenCalled()
    vi.advanceTimersByTime(999)
    expect(FakeES.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeES.instances).toHaveLength(2)
    last().fail() // failure 2 → polling starts immediately, reconnect in 2 s
    expect(poll).toHaveBeenCalledTimes(1)
    expect(states).toContain('polling')
    vi.advanceTimersByTime(2000)
    expect(FakeES.instances).toHaveLength(3)
    last().fail() // t=3 s, failure 3 → reconnect in 4 s; polling keeps going (started at t=1 s)
    vi.advanceTimersByTime(4000) // t=7 s: one 4 s tick (t=5 s) + reconnect
    expect(poll).toHaveBeenCalledTimes(2)
    expect(FakeES.instances).toHaveLength(4)
    vi.advanceTimersByTime(2000) // t=9 s: second tick
    expect(poll).toHaveBeenCalledTimes(3)
    client.stop()
  })

  it('stops polling and resyncs once SSE comes back', () => {
    const { client, poll, resync, states } = make()
    client.start()
    last().fail()
    vi.advanceTimersByTime(1000)
    last().fail()
    expect(client.polling).toBe(true)
    vi.advanceTimersByTime(2000)
    last().open()
    expect(client.polling).toBe(false)
    expect(resync).toHaveBeenCalledTimes(1)
    expect(states[states.length - 1]).toBe('open')
    const calls = poll.mock.calls.length
    vi.advanceTimersByTime(20000)
    expect(poll.mock.calls.length).toBe(calls)
    client.stop()
  })

  it('does not resync on the very first open', () => {
    const { client, resync } = make()
    client.start()
    last().open()
    expect(resync).not.toHaveBeenCalled()
    client.stop()
  })

  it('polls only when EventSource is unavailable', () => {
    const poll = vi.fn()
    const c = new RealtimeClient({ url: '/x', eventSourceFactory: null, handlers: { poll } })
    c.start()
    expect(poll).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(12000)
    expect(poll).toHaveBeenCalledTimes(4)
    c.stop()
    vi.advanceTimersByTime(12000)
    expect(poll).toHaveBeenCalledTimes(4)
  })

  it('stop() cancels reconnects and ignores late events from the old source', () => {
    const { client, message } = make()
    client.start()
    const es = last()
    es.fail()
    client.stop()
    vi.advanceTimersByTime(60000)
    expect(FakeES.instances).toHaveLength(1)
    es.emit('message', { conversation_id: 'c', message: { id: 1 } })
    expect(message).not.toHaveBeenCalled()
    expect(client.state).toBe('stopped')
  })

  it('backoff is capped and jittered', () => {
    expect(backoffDelay(1, 1000, 30000, () => 1)).toBe(1000)
    expect(backoffDelay(4, 1000, 30000, () => 1)).toBe(8000)
    expect(backoffDelay(20, 1000, 30000, () => 1)).toBe(30000)
    expect(backoffDelay(20, 1000, 30000, () => 0)).toBe(15000)
  })
})

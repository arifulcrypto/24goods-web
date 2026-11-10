import type { AfterSales, AgentStatus, Conversation, Order, StreamMessageEvent, TypingEvent } from '../types'

/** Minimal surface of EventSource we rely on (lets tests inject a fake). */
export interface EventSourceLike {
  onopen: ((ev: Event) => unknown) | null
  onerror: ((ev: Event) => unknown) | null
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void
  close(): void
}
export type EventSourceFactory = (url: string) => EventSourceLike

export type RealtimeState = 'idle' | 'connecting' | 'open' | 'polling' | 'stopped'

export interface RealtimeHandlers {
  message?: (e: StreamMessageEvent) => void
  conversation?: (c: Conversation) => void
  order?: (o: Order) => void
  agent_status?: (s: AgentStatus) => void
  /** v3: ticket opened/updated (buyer: own tickets; staff: all, with `order`/`buyer`). */
  after_sales?: (t: AfterSales) => void
  /** v11 W2: the other side is typing (ephemeral). */
  typing?: (e: TypingEvent) => void
  /** Called every `pollIntervalMs` while SSE is unavailable (fallback), first call immediately. */
  poll?: () => void | Promise<void>
  /** Called when SSE (re)opens after a disconnect: catch up anything missed (e.g. messages?after_id). */
  resync?: () => void | Promise<void>
  state?: (s: RealtimeState) => void
}

export interface RealtimeOptions {
  url: string
  handlers: RealtimeHandlers
  /** Polling interval while SSE is down. Contract: 4 s. */
  pollIntervalMs?: number
  /** First reconnect delay, doubled per consecutive failure. */
  baseDelayMs?: number
  maxDelayMs?: number
  /** Consecutive SSE failures before polling kicks in (polling continues alongside reconnect attempts). */
  failuresBeforePolling?: number
  eventSourceFactory?: EventSourceFactory | null
  /** For deterministic tests. */
  random?: () => number
}

const EVENTS = ['message', 'conversation', 'order', 'agent_status', 'after_sales', 'typing'] as const

function defaultFactory(): EventSourceFactory | null {
  const ES = (globalThis as { EventSource?: typeof EventSource }).EventSource
  if (!ES) return null
  return (url) => new ES(url, { withCredentials: false }) as unknown as EventSourceLike
}

/** Exponential backoff with jitter in [50%, 100%] of the nominal delay. */
export function backoffDelay(attempt: number, base: number, max: number, random: () => number): number {
  const nominal = Math.min(max, base * 2 ** Math.max(0, attempt - 1))
  return Math.round(nominal * (0.5 + random() * 0.5))
}

/**
 * SSE client with exponential reconnect and a polling fallback.
 * - Manages reconnection itself (closes the native EventSource on error to avoid its fixed 3 s retry loop).
 * - After `failuresBeforePolling` consecutive failures, or if EventSource is unavailable,
 *   it calls `handlers.poll()` every `pollIntervalMs` until SSE opens again.
 * - On a successful re-open after a failure it calls `handlers.resync()`.
 */
export class RealtimeClient {
  private es: EventSourceLike | null = null
  private failures = 0
  private everFailed = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pollTimer: ReturnType<typeof setInterval> | null = null
  private _state: RealtimeState = 'idle'
  private readonly o: Required<Omit<RealtimeOptions, 'eventSourceFactory'>> & {
    eventSourceFactory: EventSourceFactory | null
  }

  constructor(opts: RealtimeOptions) {
    this.o = {
      pollIntervalMs: 4000,
      baseDelayMs: 1000,
      maxDelayMs: 30000,
      failuresBeforePolling: 2,
      random: Math.random,
      ...opts,
      eventSourceFactory: opts.eventSourceFactory === undefined ? defaultFactory() : opts.eventSourceFactory,
    }
  }

  get state(): RealtimeState {
    return this._state
  }
  get polling(): boolean {
    return this.pollTimer !== null
  }

  start(): void {
    if (this._state !== 'idle' && this._state !== 'stopped') return
    if (!this.o.eventSourceFactory) {
      this.startPolling()
      return
    }
    this.connect()
  }

  stop(): void {
    this.clearReconnect()
    this.stopPolling()
    this.closeEs()
    this.failures = 0
    this.everFailed = false
    this.setState('stopped')
  }

  private setState(s: RealtimeState): void {
    if (this._state === s) return
    this._state = s
    this.o.handlers.state?.(s)
  }

  private connect(): void {
    this.clearReconnect()
    this.closeEs()
    if (!this.polling) this.setState('connecting')
    let es: EventSourceLike
    try {
      es = this.o.eventSourceFactory!(this.o.url)
    } catch {
      this.onFailure()
      return
    }
    this.es = es
    es.onopen = () => {
      if (this.es !== es) return
      const wasFailing = this.everFailed
      this.failures = 0
      this.everFailed = false
      this.stopPolling()
      this.setState('open')
      if (wasFailing) void this.safe(this.o.handlers.resync)
    }
    es.onerror = () => {
      if (this.es !== es) return
      this.closeEs()
      this.onFailure()
    }
    for (const type of EVENTS) {
      es.addEventListener(type, (ev: MessageEvent) => {
        if (this.es !== es) return
        this.dispatch(type, ev.data)
      })
    }
  }

  private dispatch(type: (typeof EVENTS)[number], raw: unknown): void {
    if (typeof raw !== 'string') return
    let data: unknown
    try {
      data = JSON.parse(raw)
    } catch {
      return
    }
    if (!data || typeof data !== 'object') return
    const h = this.o.handlers
    switch (type) {
      case 'message':
        h.message?.(data as StreamMessageEvent)
        break
      case 'conversation':
        h.conversation?.(data as Conversation)
        break
      case 'order':
        h.order?.(data as Order)
        break
      case 'agent_status':
        h.agent_status?.(data as AgentStatus)
        break
      case 'after_sales':
        h.after_sales?.(data as AfterSales)
        break
      case 'typing':
        h.typing?.(data as TypingEvent)
        break
    }
  }

  private onFailure(): void {
    this.failures++
    this.everFailed = true
    if (this.failures >= this.o.failuresBeforePolling) this.startPolling()
    else this.setState('connecting')
    const delay = backoffDelay(this.failures, this.o.baseDelayMs, this.o.maxDelayMs, this.o.random)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private startPolling(): void {
    this.setState('polling')
    if (this.pollTimer) return
    void this.safe(this.o.handlers.poll)
    this.pollTimer = setInterval(() => void this.safe(this.o.handlers.poll), this.o.pollIntervalMs)
  }

  private stopPolling(): void {
    if (this.pollTimer) clearInterval(this.pollTimer)
    this.pollTimer = null
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }

  private closeEs(): void {
    if (this.es) {
      this.es.onopen = null
      this.es.onerror = null
      try {
        this.es.close()
      } catch {
        /* ignore */
      }
    }
    this.es = null
  }

  private async safe(fn?: () => void | Promise<void>): Promise<void> {
    if (!fn) return
    try {
      await fn()
    } catch {
      /* the next tick retries */
    }
  }
}

/** Tiny typed event emitter used by the apps' realtime stores. */
export function createEmitter<E extends Record<string, unknown>>() {
  const map = new Map<keyof E, Set<(p: never) => void>>()
  return {
    on<K extends keyof E>(k: K, fn: (p: E[K]) => void): () => void {
      let set = map.get(k)
      if (!set) map.set(k, (set = new Set()))
      set.add(fn as (p: never) => void)
      return () => set!.delete(fn as (p: never) => void)
    },
    emit<K extends keyof E>(k: K, p: E[K]): void {
      map.get(k)?.forEach((fn) => {
        try {
          ;(fn as (p: E[K]) => void)(p)
        } catch {
          /* isolate listeners */
        }
      })
    },
  }
}

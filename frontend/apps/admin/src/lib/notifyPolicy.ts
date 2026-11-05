/**
 * CS notification policy (review-v3 §6). Pure decision logic, unit-tested; the side effects
 * (sound, Notification, document.title) live in stores/notifications.ts.
 */

export type NotifyKind = 'message' | 'order' | 'after_sales'

export interface NotifyContext {
  /** document.visibilityState === 'hidden' or the window has no focus. */
  hidden: boolean
  /** Conversation currently open in the Inbox (null when not on the Inbox / none selected). */
  openConversationId: string | null
  /** Order currently open in a drawer, if known. */
  openOrderId?: string | null
}

export interface NotifyEvent {
  kind: NotifyKind
  conversationId?: string | null
  orderId?: string | null
}

/**
 * Should this event alert the agent at all?
 * Only when the tab is hidden, or the agent is looking at something else.
 */
export function isRelevant(ev: NotifyEvent, ctx: NotifyContext): boolean {
  if (ctx.hidden) return true
  if (ev.kind === 'message') return !ev.conversationId || ev.conversationId !== ctx.openConversationId
  if (ev.kind === 'order' && ev.orderId && ctx.openOrderId === ev.orderId) return false
  return true
}

export const THROTTLE_MS = 4000

/** Leading-edge throttle: at most one alert per `ms`. */
export function createThrottle(ms = THROTTLE_MS, now: () => number = Date.now) {
  let last = -Infinity
  return {
    /** true if an alert may fire now (and records it). */
    take(): boolean {
      const t = now()
      if (t - last < ms) return false
      last = t
      return true
    },
    reset(): void {
      last = -Infinity
    },
  }
}

/** "(3) Kesiv Console" — unread counter in the tab title. */
export function titleWithCount(base: string, count: number): string {
  if (count <= 0) return base
  return `(${count > 99 ? '99+' : count}) ${base}`
}

/** Bounded "already notified" set (e.g. order ids that became paid). */
export function createSeenSet(max = 500) {
  const s = new Set<string>()
  return {
    /** true the first time an id is seen. */
    first(id: string): boolean {
      if (s.has(id)) return false
      s.add(id)
      if (s.size > max) s.delete(s.values().next().value as string)
      return true
    },
  }
}

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

// ---- order lifecycle alerts (review-v8b §4) ----
export type OrderAlertKind = 'created' | 'paid' | 'cancelled' | 'expired'
export type OrderAlertPrefs = Record<OrderAlertKind, boolean>
export const DEFAULT_ORDER_ALERTS: Readonly<OrderAlertPrefs> = { created: true, paid: true, cancelled: true, expired: false }
export const ORDER_ALERT_KINDS = Object.keys(DEFAULT_ORDER_ALERTS) as OrderAlertKind[]

/** Merge stored (untrusted) toggles over the defaults; anything that is not a boolean is ignored. */
export function mergeOrderAlerts(raw: unknown): OrderAlertPrefs {
  const out = { ...DEFAULT_ORDER_ALERTS }
  if (raw && typeof raw === 'object') {
    for (const k of ORDER_ALERT_KINDS) {
      const v = (raw as Record<string, unknown>)[k]
      if (typeof v === 'boolean') out[k] = v
    }
  }
  return out
}

/** A paid status change older than this is a late staff action, not a payment arriving now. */
export const PAID_FRESH_MS = 10 * 60 * 1000
export const CREATED_FRESH_MS = 2 * 60 * 1000
const PAID_STATES = new Set(['paid', 'delivered', 'completed', 'manual_review'])

/**
 * Map a staff `order` event to at most one alert, exactly once per (order, kind): a manual order
 * going paid -> delivered never alerts twice, and every delivery mode alerts on payment.
 */
export function orderAlertKind(
  o: { id: string; status: string; paid_at?: string | null; created_at?: string | null },
  seen: { first(id: string): boolean },
  now: number = Date.now(),
): OrderAlertKind | null {
  let kind: OrderAlertKind | null = null
  if (o.status === 'pending_payment') {
    // re-broadcasts of an old pending order (e.g. channel switch) are not new orders
    const c = o.created_at ? Date.parse(o.created_at) : NaN
    kind = Number.isFinite(c) && now - c <= CREATED_FRESH_MS ? 'created' : null
  }
  else if (o.status === 'cancelled') kind = 'cancelled'
  else if (o.status === 'expired') kind = 'expired'
  else if (PAID_STATES.has(o.status)) {
    const t = o.paid_at ? Date.parse(o.paid_at) : NaN
    kind = Number.isFinite(t) && now - t <= PAID_FRESH_MS ? 'paid' : null
  }
  return kind && seen.first(`${kind}:${o.id}`) ? kind : null
}

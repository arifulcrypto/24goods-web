import type { PaymentGatewayAdmin, TestPaymentView } from '@24goods/shared'

/** Gateways in the order the admin sees them: by priority (the server already sorts, this keeps the
 *  UI stable while an optimistic move is pending). */
export function byPriority(items: PaymentGatewayAdmin[]): PaymentGatewayAdmin[] {
  return [...items].sort((a, b) => a.priority - b.priority || a.created_at.localeCompare(b.created_at))
}

/** Ids after moving ``id`` one place up (-1) or down (+1); unchanged at the ends / unknown id. */
export function moveId(ids: string[], id: string, dir: -1 | 1): string[] {
  const i = ids.indexOf(id)
  const j = i + dir
  if (i < 0 || j < 0 || j >= ids.length) return ids
  const next = [...ids]
  ;[next[i], next[j]] = [next[j], next[i]]
  return next
}

/** 1-based place the next payment will try this gateway, or null when it is disabled. */
export function tryOrder(candidates: string[], id: string): number | null {
  const i = candidates.indexOf(id)
  return i < 0 ? null : i + 1
}

export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export type TestPhase = 'waiting' | 'paid' | 'expired' | 'failed'

/** Dialog state from a test-payment view and the local countdown. */
export function testPhase(view: Pick<TestPaymentView, 'status'> | null, secondsLeft: number): TestPhase {
  if (!view) return 'waiting'
  if (view.status === 'paid') return 'paid'
  if (view.status === 'failed') return 'failed'
  if (view.status === 'expired' || secondsLeft <= 0) return 'expired'
  return 'waiting'
}

/** Keep polling only while the payment can still change. */
export function shouldPoll(phase: TestPhase): boolean {
  return phase === 'waiting'
}

const AMOUNT = /^(?:10(?:\.00?)?|[0-9](?:\.\d{1,2})?|0\.\d{1,2})$/

/** Client-side hint only: the server enforces 0.01..10.00 with at most 2 decimals. */
export function validTestAmount(raw: string): boolean {
  const v = raw.trim()
  if (!AMOUNT.test(v)) return false
  const n = Number(v)
  return n >= 0.01 && n <= 10
}

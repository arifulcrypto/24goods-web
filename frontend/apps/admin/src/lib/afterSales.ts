/** After-sales desk rules (review-v3 §7). Pure, unit-tested; the server re-checks everything. */
import type { DeliveryMode } from '@24goods/shared'

export const TICKET_STATUSES = ['open', 'processing', 'replaced', 'refunded', 'rejected', 'resolved'] as const
export type TicketStatus = (typeof TICKET_STATUSES)[number]
export const TICKET_REASONS = ['not_received', 'invalid_code', 'wrong_item', 'account_issue', 'other'] as const
export type TicketReason = (typeof TICKET_REASONS)[number]
export type TicketAction = 'processing' | 'replace' | 'refund' | 'reject' | 'resolve'

/** Ticket is still being worked on (the partial UNIQUE index covers exactly these). */
export function isOpenStatus(s: TicketStatus): boolean {
  return s === 'open' || s === 'processing'
}

/**
 * Actions offered for a ticket in a given status.
 * - open: mark processing, replace, refund, reject, resolve
 * - processing: replace, refund, reject, resolve
 * - replaced: resolve (close after the buyer confirmed), or refund if the replacement also failed
 * - refunded / rejected / resolved: terminal
 */
export function availableActions(status: TicketStatus): TicketAction[] {
  switch (status) {
    case 'open':
      return ['processing', 'replace', 'refund', 'reject', 'resolve']
    case 'processing':
      return ['replace', 'refund', 'reject', 'resolve']
    case 'replaced':
      return ['refund', 'resolve']
    default:
      return []
  }
}

/** Destructive actions need an explicit confirm dialog. */
export function needsConfirm(a: TicketAction): boolean {
  return a === 'replace' || a === 'refund' || a === 'reject'
}

/** Body validation per action (returns an i18n error key or null). */
export function validateAction(
  a: TicketAction,
  input: { note?: string; delivery_text?: string },
  mode: DeliveryMode,
): string | null {
  if (a === 'reject' && !input.note?.trim()) return 'afterSales.v.reasonRequired'
  if (a === 'replace' && mode === 'manual' && !input.delivery_text?.trim()) return 'afterSales.v.deliveryRequired'
  return null
}

export function statusTagType(s: TicketStatus): 'warning' | 'primary' | 'success' | 'danger' | 'info' {
  switch (s) {
    case 'open':
      return 'warning'
    case 'processing':
      return 'primary'
    case 'replaced':
    case 'resolved':
      return 'success'
    case 'refunded':
      return 'danger'
    default:
      return 'info'
  }
}

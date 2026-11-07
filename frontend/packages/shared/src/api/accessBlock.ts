import { shallowRef } from 'vue'
import { errorExtra, isApiError } from './errors'

/** Set when the API refuses the client outright (blocked IP / suspended account): the app shows a
 *  full-page screen (`AccessBlockedView`) instead of a toast. */
export interface AccessBlock {
  kind: 'ip_blocked' | 'account_suspended'
  reason?: string
  until?: string
}

export const accessBlock = shallowRef<AccessBlock | null>(null)

export function reportAccessBlock(err: unknown): boolean {
  if (!isApiError(err) || (err.code !== 'ip_blocked' && err.code !== 'account_suspended')) return false
  const x = errorExtra(err)
  accessBlock.value = {
    kind: err.code,
    reason: typeof x.reason === 'string' ? x.reason : undefined,
    until: typeof x.until === 'string' ? x.until : undefined,
  }
  return true
}

/**
 * Opening customer service from the storefront.
 *
 * Staff browse the shop with their own session cookie, so "Contact support" would otherwise open
 * a buyer conversation owned by the staff member themselves — a thread no buyer can ever see and
 * that the console lists as its own customer. Staff are told what account they are on and sent to
 * the console inbox instead; the router keeps /chat closed to them as a backstop.
 */
import type { Router } from 'vue-router'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import { confirm } from '../ui'

export const ADMIN_INBOX_URL = '/admin/inbox'

/** True when the signed-in account is staff and must not use the buyer chat. */
export function staffOnStorefront(): boolean {
  return useAuthStore().isStaff
}

/** Tells staff they are on a staff account and offers the console. Returns true if it handled it. */
export async function interceptStaffSupport(): Promise<boolean> {
  if (!staffOnStorefront()) return false
  const t = i18n.global.t
  if (await confirm(t('cs.staffTitle'), t('cs.staffText'))) window.location.assign(ADMIN_INBOX_URL)
  return true
}

/** Opens buyer support, unless the caller is staff. */
export async function openSupport(router: Router, productId?: string): Promise<void> {
  if (await interceptStaffSupport()) return
  await router.push(productId ? { path: '/chat', query: { product: productId } } : '/chat')
}

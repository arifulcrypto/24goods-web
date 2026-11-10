import { onBeforeUnmount, ref } from 'vue'
import type { SenderRole, TypingEvent } from '../types'

/** How long a received "typing…" hint stays without a refresh (server sends one per ≤ 3 s). */
export const TYPING_SHOW_MS = 6000
/** Minimum gap between our own typing pings (the server enforces 1 per 3 s). */
export const TYPING_PING_MS = 3000

/**
 * Typing indicator for one conversation. `onEvent` feeds SSE events of the other side,
 * `clearFor(role)` hides it when a message from that side arrives, `ping()` is called from the
 * composer on every input and sends at most one request per 3 s (errors are ignored: it is a hint).
 */
export function useTyping(opts: { send: () => Promise<unknown> | void; conversationId: () => string | null; otherRole: SenderRole }) {
  const name = ref<string | null>(null)
  let hideTimer: ReturnType<typeof setTimeout> | null = null
  let lastPing = 0

  function clear() {
    if (hideTimer) clearTimeout(hideTimer)
    hideTimer = null
    name.value = null
  }
  function onEvent(e: TypingEvent) {
    if (e.role !== opts.otherRole || e.conversation_id !== opts.conversationId()) return
    name.value = e.name || ''
    if (hideTimer) clearTimeout(hideTimer)
    hideTimer = setTimeout(clear, TYPING_SHOW_MS)
  }
  function clearFor(role: SenderRole, conversationId: string) {
    if (role === opts.otherRole && conversationId === opts.conversationId()) clear()
  }
  function ping(now = Date.now()) {
    if (!opts.conversationId() || now - lastPing < TYPING_PING_MS) return
    lastPing = now
    try {
      void Promise.resolve(opts.send()).catch(() => undefined)
    } catch {
      /* hint only */
    }
  }
  onBeforeUnmount(clear)
  return { name, onEvent, clearFor, clear, ping }
}

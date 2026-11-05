import { computed, ref, shallowRef } from 'vue'
import type { Attachment, Message, MessageQuery, QuoteCardPayload, SendMessageBody, SenderRole } from '../types'
import { ApiError, isApiError } from '../api/errors'
import { uuid } from '../util'
import { mergeMessages } from './merge'

export interface ChatAdapter {
  list(conversationId: string, q: MessageQuery): Promise<Message[]>
  send(conversationId: string, body: SendMessageBody): Promise<Message>
  upload(file: Blob, o: { kind: 'image' | 'voice'; durationMs?: number; filename?: string }): Promise<Attachment>
  markRead?(conversationId: string, messageId: number): Promise<void>
}

export type PendingState = 'sending' | 'failed'
/** A locally-composed message not yet acknowledged by the server. */
export interface PendingMessage {
  client_msg_id: string
  type: 'text' | 'image' | 'voice'
  text: string | null
  /** Local blob for image/voice until the upload succeeds. */
  blob: Blob | null
  filename?: string
  objectUrl: string | null
  durationMs: number | null
  width?: number
  height?: number
  attachment: Attachment | null
  state: PendingState
  error: ApiError | null
  created_at: string
}

/** Item rendered by the message list: a server message or a pending local one. */
export type ChatItem =
  | { kind: 'server'; key: string; message: Message }
  | { kind: 'pending'; key: string; pending: PendingMessage; senderRole: SenderRole }

export interface UseConversationOptions {
  adapter: ChatAdapter
  /** Which role the current user sends as (for rendering pending bubbles). */
  selfRole: 'buyer' | 'agent'
  pageSize?: number
  onError?: (e: unknown) => void
}

/** Chat state for one conversation: initial load, older pages, catch-up, optimistic send + retry. */
export function useConversation(opts: UseConversationOptions) {
  const pageSize = opts.pageSize ?? 30
  const conversationId = ref<string | null>(null)
  const messages = shallowRef<Message[]>([])
  const pending = ref<PendingMessage[]>([])
  const loading = ref(false)
  const loadingOlder = ref(false)
  const hasMore = ref(true)
  const error = ref<unknown>(null)
  let generation = 0
  let catchingUp: Promise<number> | null = null
  let lastReadSent = 0

  const items = computed<ChatItem[]>(() => [
    ...messages.value.map((m) => ({ kind: 'server' as const, key: `m${m.id}`, message: m })),
    ...pending.value.map((p) => ({
      kind: 'pending' as const,
      key: `p${p.client_msg_id}`,
      pending: p,
      senderRole: opts.selfRole as SenderRole,
    })),
  ])
  const lastId = computed(() => messages.value[messages.value.length - 1]?.id ?? 0)

  function reset() {
    generation++
    for (const p of pending.value) if (p.objectUrl) URL.revokeObjectURL(p.objectUrl)
    messages.value = []
    pending.value = []
    hasMore.value = true
    error.value = null
    lastReadSent = 0
  }

  function apply(incoming: Message[]) {
    if (!incoming.length) return
    const cid = conversationId.value
    const mine = incoming.filter((m) => m.conversation_id === cid)
    if (!mine.length) return
    messages.value = mergeMessages(messages.value, mine)
    const acked = new Set(mine.map((m) => m.client_msg_id).filter((x): x is string => !!x))
    if (acked.size) {
      const keep: PendingMessage[] = []
      for (const p of pending.value) {
        if (acked.has(p.client_msg_id)) {
          if (p.objectUrl) setTimeout(() => URL.revokeObjectURL(p.objectUrl!), 30_000)
        } else keep.push(p)
      }
      pending.value = keep
    }
  }

  async function open(id: string): Promise<void> {
    reset()
    conversationId.value = id
    const gen = generation
    loading.value = true
    try {
      const list = await opts.adapter.list(id, { limit: pageSize })
      if (gen !== generation) return
      messages.value = mergeMessages([], list)
      hasMore.value = list.length >= pageSize
    } catch (e) {
      if (gen === generation) {
        error.value = e
        opts.onError?.(e)
      }
    } finally {
      if (gen === generation) loading.value = false
    }
  }

  async function loadOlder(): Promise<number> {
    const id = conversationId.value
    const first = messages.value[0]
    if (!id || !first || !hasMore.value || loadingOlder.value || loading.value) return 0
    const gen = generation
    loadingOlder.value = true
    try {
      const list = await opts.adapter.list(id, { before_id: first.id, limit: pageSize })
      if (gen !== generation) return 0
      hasMore.value = list.length >= pageSize
      const before = messages.value.length
      messages.value = mergeMessages(messages.value, list)
      return messages.value.length - before
    } catch (e) {
      opts.onError?.(e)
      return 0
    } finally {
      if (gen === generation) loadingOlder.value = false
    }
  }

  /** Fetch everything after the newest known id (polling fallback / SSE resync). */
  function catchUp(): Promise<number> {
    if (catchingUp) return catchingUp
    const run = async () => {
      const id = conversationId.value
      if (!id || loading.value) return 0
      const gen = generation
      let total = 0
      for (let i = 0; i < 10; i++) {
        const list = await opts.adapter.list(id, { after_id: lastId.value, limit: 100 })
        if (gen !== generation) return total
        apply(list)
        total += list.length
        if (list.length < 100) break
      }
      return total
    }
    catchingUp = run().finally(() => {
      catchingUp = null
    })
    return catchingUp
  }

  async function deliver(p: PendingMessage): Promise<void> {
    const id = conversationId.value
    if (!id) return
    const gen = generation
    p.state = 'sending'
    p.error = null
    try {
      if ((p.type === 'image' || p.type === 'voice') && !p.attachment) {
        if (!p.blob) throw new ApiError('validation', 'missing file', 0)
        p.attachment = await opts.adapter.upload(p.blob, {
          kind: p.type,
          durationMs: p.durationMs ?? undefined,
          filename: p.filename,
        })
      }
      const body: SendMessageBody =
        p.type === 'text'
          ? { client_msg_id: p.client_msg_id, type: 'text', text: p.text ?? '' }
          : { client_msg_id: p.client_msg_id, type: p.type, attachment_id: p.attachment!.id }
      const msg = await opts.adapter.send(id, body)
      if (gen !== generation) return
      apply([msg])
      // If the server did not echo client_msg_id, drop the pending bubble anyway.
      pending.value = pending.value.filter((x) => x.client_msg_id !== p.client_msg_id)
    } catch (e) {
      if (gen !== generation) return
      const target = pending.value.find((x) => x.client_msg_id === p.client_msg_id)
      if (target) {
        target.state = 'failed'
        target.error = isApiError(e) ? e : new ApiError('unknown', String(e), 0)
      }
      opts.onError?.(e)
    }
  }

  function enqueue(p: Omit<PendingMessage, 'client_msg_id' | 'state' | 'error' | 'created_at' | 'attachment'>) {
    const item: PendingMessage = {
      ...p,
      client_msg_id: uuid(),
      attachment: null,
      state: 'sending',
      error: null,
      created_at: new Date().toISOString(),
    }
    pending.value = [...pending.value, item]
    // operate on the reactive proxy so state changes re-render
    const reactiveItem = pending.value[pending.value.length - 1]!
    void deliver(reactiveItem)
    return reactiveItem
  }

  function sendText(text: string) {
    const t = text.trim()
    if (!t) return null
    return enqueue({ type: 'text', text: t.slice(0, 2000), blob: null, objectUrl: null, durationMs: null })
  }
  function sendImage(blob: Blob, meta: { width?: number; height?: number; filename?: string } = {}) {
    return enqueue({
      type: 'image',
      text: null,
      blob,
      filename: meta.filename,
      width: meta.width,
      height: meta.height,
      objectUrl: URL.createObjectURL(blob),
      durationMs: null,
    })
  }
  function sendVoice(blob: Blob, durationMs: number, filename?: string) {
    return enqueue({
      type: 'voice',
      text: null,
      blob,
      filename,
      objectUrl: URL.createObjectURL(blob),
      durationMs,
    })
  }
  function retry(clientMsgId: string) {
    const p = pending.value.find((x) => x.client_msg_id === clientMsgId)
    if (p && p.state === 'failed') void deliver(p)
  }
  function discard(clientMsgId: string) {
    const p = pending.value.find((x) => x.client_msg_id === clientMsgId)
    if (p?.objectUrl) URL.revokeObjectURL(p.objectUrl)
    pending.value = pending.value.filter((x) => x.client_msg_id !== clientMsgId)
  }

  /** Locally patch quote cards after accept/withdraw so the UI reflects it before the server echo. */
  function patchQuote(q: Partial<QuoteCardPayload> & { quote_id: string }) {
    let changed = false
    const next = messages.value.map((m) => {
      if (m.type === 'quote_card' && m.payload && (m.payload as QuoteCardPayload).quote_id === q.quote_id) {
        changed = true
        return { ...m, payload: { ...(m.payload as QuoteCardPayload), ...q } }
      }
      return m
    })
    if (changed) messages.value = next
  }

  async function markRead(): Promise<void> {
    const id = conversationId.value
    const last = lastId.value
    if (!id || !last || last <= lastReadSent || !opts.adapter.markRead) return
    lastReadSent = last
    try {
      await opts.adapter.markRead(id, last)
    } catch {
      lastReadSent = 0
    }
  }

  return {
    conversationId,
    messages,
    pending,
    items,
    loading,
    loadingOlder,
    hasMore,
    error,
    lastId,
    open,
    reset,
    apply,
    loadOlder,
    catchUp,
    sendText,
    sendImage,
    sendVoice,
    retry,
    discard,
    patchQuote,
    markRead,
  }
}
export type ConversationState = ReturnType<typeof useConversation>

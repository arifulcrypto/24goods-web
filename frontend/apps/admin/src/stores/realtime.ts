import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  RealtimeClient,
  createEmitter,
  type AgentStatus,
  type Conversation,
  type Order,
  type RealtimeState,
  type StreamMessageEvent,
  type TypingEvent,
} from '@24goods/shared'
import { api } from '../api'
import type { AfterSalesTicket } from '../api-v3'

type Events = {
  message: StreamMessageEvent
  conversation: Conversation
  order: Order
  agent_status: AgentStatus
  typing: TypingEvent
  after_sales: AfterSalesTicket
  sync: void
}

/** Staff SSE (/admin/stream: all conversation, message, order and after-sales events of the tenant). */
export const useRealtimeStore = defineStore('realtime', () => {
  const state = ref<RealtimeState>('idle')
  const bus = createEmitter<Events>()
  let client: RealtimeClient | null = null

  function start() {
    if (client) return
    client = new RealtimeClient({
      url: api.streamUrl(),
      handlers: {
        message: (e) => bus.emit('message', e),
        conversation: (c) => bus.emit('conversation', c),
        order: (o) => bus.emit('order', o),
        agent_status: (s) => bus.emit('agent_status', s),
        typing: (e) => bus.emit('typing', e),
        after_sales: (t) => bus.emit('after_sales', t as unknown as AfterSalesTicket),
        poll: () => bus.emit('sync', undefined),
        resync: () => bus.emit('sync', undefined),
        state: (s) => (state.value = s),
      },
    })
    client.start()
  }
  function stop() {
    client?.stop()
    client = null
    state.value = 'idle'
  }
  return { state, start, stop, on: bus.on }
})

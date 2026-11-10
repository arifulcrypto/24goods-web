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

type Events = {
  message: StreamMessageEvent
  conversation: Conversation
  order: Order
  agent_status: AgentStatus
  typing: TypingEvent
  /** Polling tick (SSE down) or resync after reconnect: pages should catch up. */
  sync: void
}

/** One SSE connection per tab (buyer /stream), shared by all pages via an emitter. */
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

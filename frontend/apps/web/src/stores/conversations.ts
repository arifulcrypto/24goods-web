import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Conversation } from '@24goods/shared'
import { api } from '../api'

export const useConversationsStore = defineStore('conversations', () => {
  const list = ref<Conversation[]>([])
  const loaded = ref(false)
  /** Conversation currently on screen (its unread count is suppressed). */
  const activeId = ref<string | null>(null)

  const totalUnread = computed(() =>
    list.value.reduce((n, c) => n + (c.id === activeId.value ? 0 : Math.max(0, c.unread)), 0),
  )

  async function refresh() {
    list.value = sortList(await api.conversations.list())
    loaded.value = true
  }
  function upsert(c: Conversation) {
    const i = list.value.findIndex((x) => x.id === c.id)
    const next = [...list.value]
    if (i >= 0) next[i] = c
    else next.push(c)
    list.value = sortList(next)
  }
  function bumpUnread(conversationId: string, lastMessage: Conversation['last_message']) {
    const c = list.value.find((x) => x.id === conversationId)
    if (!c) return false
    upsert({
      ...c,
      unread: conversationId === activeId.value ? 0 : c.unread + 1,
      last_message: lastMessage,
      updated_at: lastMessage?.created_at ?? c.updated_at,
    })
    return true
  }
  function markRead(id: string) {
    const c = list.value.find((x) => x.id === id)
    if (c && c.unread) upsert({ ...c, unread: 0 })
  }
  function reset() {
    list.value = []
    loaded.value = false
    activeId.value = null
  }
  return { list, loaded, activeId, totalUnread, refresh, upsert, bumpUnread, markRead, reset }
})

function sortList(l: Conversation[]): Conversation[] {
  return [...l].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
}

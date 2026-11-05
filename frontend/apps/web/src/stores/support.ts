import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AgentStatus } from '@24goods/shared'
import { api } from '../api'

export const useSupportStore = defineStore('support', () => {
  const status = ref<AgentStatus | null>(null)
  let fetchedAt = 0
  async function load(force = false) {
    if (!force && Date.now() - fetchedAt < 30_000 && status.value) return
    try {
      status.value = await api.supportStatus()
      fetchedAt = Date.now()
    } catch {
      /* non-critical */
    }
  }
  function set(s: AgentStatus) {
    status.value = s
    fetchedAt = Date.now()
  }
  return { status, load, set }
})

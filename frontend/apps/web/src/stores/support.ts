import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { AgentStatus } from '@24goods/shared'
import { api } from '../api'
import { siteAccessCleared } from '../lib/securityChallenge'

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
  // a 428 left the status empty: fetch it again once the visitor has cleared the access check
  watch(siteAccessCleared, () => {
    if (!status.value) void load(true)
  })
  function set(s: AgentStatus) {
    status.value = s
    fetchedAt = Date.now()
  }
  return { status, load, set }
})

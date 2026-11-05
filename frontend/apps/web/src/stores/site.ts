import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { SiteInfo } from '@24goods/shared'
import { api } from '../api'

export const useSiteStore = defineStore('site', () => {
  const site = ref<SiteInfo | null>(null)
  let loading: Promise<void> | null = null
  function load(): Promise<void> {
    if (site.value) return Promise.resolve()
    loading ??= api
      .site()
      .then((s) => {
        site.value = s
      })
      .catch(() => undefined)
      .finally(() => {
        loading = null
      })
    return loading
  }
  return { site, load }
})

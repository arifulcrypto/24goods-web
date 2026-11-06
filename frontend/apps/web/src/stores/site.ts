import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { PaymentChannel, SiteInfo } from '@24goods/shared'
import { DEFAULT_PAY_CHANNEL } from '@24goods/shared'
import { api } from '../api'

export const useSiteStore = defineStore('site', () => {
  const site = ref<SiteInfo | null>(null)
  const channels = ref<PaymentChannel[] | null>(null)
  let loading: Promise<void> | null = null
  let loadingChannels: Promise<void> | null = null
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
  /** GET /site/payment-channels: the fee is shown before an order exists (never trusted). */
  function loadChannels(): Promise<void> {
    if (channels.value) return Promise.resolve()
    loadingChannels ??= api
      .paymentChannels()
      .then((c) => {
        channels.value = c
      })
      .catch(() => undefined)
      .finally(() => {
        loadingChannels = null
      })
    return loadingChannels
  }
  /** The channel orders are created on (single channel today). */
  const channel = computed<PaymentChannel | null>(
    () => channels.value?.find((c) => c.id === DEFAULT_PAY_CHANNEL) ?? channels.value?.[0] ?? null,
  )
  const permille = computed(() => channel.value?.permille ?? 0)
  return { site, channels, channel, permille, load, loadChannels }
})

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { PaymentChannel, SiteFeatures, SiteInfo } from '@24goods/shared'
import { BRAND, DEFAULT_PAY_CHANNEL, isWhiteLabel, promoAvailable } from '@24goods/shared'
import { currentLocale } from '../i18n'
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
  /** Feature toggles: everything defaults to on (older backends / not loaded yet), resellers to off. */
  const features = computed<SiteFeatures>(() => ({
    registration: true,
    guest_checkout: true,
    cart: true,
    promo_codes: true,
    resellers: false,
    voice_messages: true,
    lookup: true,
    google_login: false,
    ...(site.value?.features ?? {}),
  }))
  const storefront = computed(() => site.value?.storefront ?? null)
  const showPromo = computed(() => promoAvailable(site.value))
  const googleLogin = computed(() => site.value?.google_login === true && features.value.google_login)
  /** general.site_name_i18n override, else the brand.ts name (zh-CN: 客思维). Storefront name wins in the header. */
  const siteName = computed(() => {
    const loc = currentLocale()
    const o = site.value?.general?.site_name_i18n?.[loc]?.trim()
    if (o) return o
    return loc === 'zh-CN' ? BRAND.nameZh : BRAND.name
  })
  const whiteLabel = computed(() => isWhiteLabel(site.value))
  /** Name shown wherever the brand would appear: the store name on a white-label storefront. */
  const headerName = computed(() => (whiteLabel.value ? storefront.value!.store_name : siteName.value))
  const legal = computed(() => (site.value?.legal?.enabled ? site.value.legal : null))
  const manualNoteRaw = computed(() => site.value?.delivery?.manual_note ?? null)
  const manualDefaultHours = computed(() => site.value?.delivery?.manual_eta_hours ?? 24)
  return {
    site,
    channels,
    channel,
    permille,
    load,
    loadChannels,
    features,
    storefront,
    showPromo,
    googleLogin,
    siteName,
    headerName,
    whiteLabel,
    legal,
    manualNoteRaw,
    manualDefaultHours,
  }
})

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fromMinor, formatMoney, type ResellerMe } from '@24goods/shared'
import { api } from '../api'

export const useResellerStore = defineStore('reseller', () => {
  const me = ref<ResellerMe | null>(null)
  const loaded = ref(false)
  async function load(force = false): Promise<ResellerMe | null> {
    if (loaded.value && !force) return me.value
    me.value = await api.reseller.me()
    loaded.value = true
    return me.value
  }
  function set(m: ResellerMe) {
    me.value = m
    loaded.value = true
  }
  return { me, loaded, load, set }
})

/** Minor units (int) → localized money string. */
export function minorMoney(n: number, currency: string, locale: string): string {
  return formatMoney(fromMinor(BigInt(Math.trunc(n))), currency, locale)
}

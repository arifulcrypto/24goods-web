import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiV3 } from '../api'

/** Shared counters for the sidebar (open after-sales tickets) and the tab-title unread count. */
export const useDeskStore = defineStore('desk', () => {
  const afterSalesOpen = ref(0)
  const unread = ref(0)
  let pending: ReturnType<typeof setTimeout> | null = null

  async function refreshAfterSales(): Promise<void> {
    try {
      const [open, processing] = await Promise.all([
        apiV3.afterSales.list({ status: 'open', page: 1, page_size: 1 }),
        apiV3.afterSales.list({ status: 'processing', page: 1, page_size: 1 }),
      ])
      afterSalesOpen.value = (open.total ?? 0) + (processing.total ?? 0)
    } catch {
      /* the badge is best-effort (e.g. endpoint not deployed yet) */
    }
  }
  /** Coalesce bursts of after-sales events into one refresh. */
  function refreshAfterSalesSoon(): void {
    if (pending) return
    pending = setTimeout(() => {
      pending = null
      void refreshAfterSales()
    }, 1000)
  }
  return { afterSalesOpen, unread, refreshAfterSales, refreshAfterSalesSoon }
})

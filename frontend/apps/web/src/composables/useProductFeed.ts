import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CatalogSort, ProductSummary } from '@24goods/shared'
import { api } from '../api'

/**
 * Paginated product feed for van-list infinite scroll.
 *
 * Titles come back localized by the server (Accept-Language), so the feed is keyed by locale too:
 * a language switch drops the cached pages and refetches the same query/category, and a response
 * still in flight from the old locale is discarded by the generation check (issue 49 D).
 */
export function useProductFeed(params: () => { q?: string; category?: string; sort?: CatalogSort }, pageSize = 20) {
  const items = ref<ProductSummary[]>([])
  const loading = ref(false)
  const finished = ref(false)
  const error = ref(false)
  const total = ref<number | null>(null)
  let page = 0
  let gen = 0
  let started = false

  async function load() {
    started = true
    const myGen = gen
    loading.value = true
    error.value = false
    try {
      const r = await api.products({ ...params(), page: page + 1, page_size: pageSize })
      if (myGen !== gen) return
      page = r.page || page + 1
      const seen = new Set(items.value.map((x) => x.id))
      items.value = [...items.value, ...r.items.filter((x) => !seen.has(x.id))]
      total.value = r.total
      finished.value = r.items.length === 0 || items.value.length >= r.total
    } catch {
      if (myGen === gen) error.value = true
    } finally {
      if (myGen === gen) loading.value = false
    }
  }

  function reset() {
    started = false
    gen++
    page = 0
    items.value = []
    total.value = null
    finished.value = false
    error.value = false
    loading.value = false
  }

  const { locale } = useI18n()
  watch(locale, () => {
    if (!started) return
    reset()
    void load()
  })

  return { items, loading, finished, error, total, load, reset }
}

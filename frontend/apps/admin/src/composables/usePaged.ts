import { ref, shallowRef } from 'vue'
import type { Paginated } from '@24goods/shared'
import { toastError } from '../ui'

/** Generic paginated table state for Element Plus tables. */
export function usePaged<T>(fetcher: (page: number, pageSize: number) => Promise<Paginated<T>>, pageSize = 20) {
  const items = shallowRef<T[]>([])
  const total = ref(0)
  const page = ref(1)
  const loading = ref(false)
  let gen = 0

  async function load(p = page.value) {
    const my = ++gen
    loading.value = true
    try {
      const r = await fetcher(p, pageSize)
      if (my !== gen) return
      items.value = r.items
      total.value = r.total
      page.value = r.page || p
    } catch (e) {
      if (my === gen) toastError(e)
    } finally {
      if (my === gen) loading.value = false
    }
  }
  function reload() {
    return load(1)
  }
  function patch(pred: (x: T) => boolean, next: T) {
    const i = items.value.findIndex(pred)
    if (i >= 0) items.value = items.value.map((x, j) => (j === i ? next : x))
  }
  return { items, total, page, pageSize, loading, load, reload, patch }
}

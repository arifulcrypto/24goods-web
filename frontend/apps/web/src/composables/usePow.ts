import { onBeforeUnmount, ref } from 'vue'
import { PowAbortError, solvePow, type PowScope, type PowSolution } from '@24goods/shared'
import { api } from '../api'

/** Fetch a challenge for `scope` and solve it (Web Worker, JS fallback) with a progress value. */
export function usePow() {
  const solving = ref(false)
  const progress = ref(0)
  let ctrl: AbortController | null = null

  async function run(scope: PowScope): Promise<PowSolution> {
    ctrl?.abort()
    const c = new AbortController()
    ctrl = c
    solving.value = true
    progress.value = 0
    try {
      const ch = await api.pow.challenge(scope)
      return await solvePow(ch, { signal: c.signal, onProgress: (p) => (progress.value = p) })
    } finally {
      if (ctrl === c) solving.value = false
    }
  }
  function cancel() {
    ctrl?.abort()
    solving.value = false
  }
  onBeforeUnmount(cancel)
  return { solving, progress, run, cancel }
}

export function isPowAbort(e: unknown): boolean {
  return e instanceof PowAbortError
}

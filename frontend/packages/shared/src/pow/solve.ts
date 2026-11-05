import type { PowChallenge, PowSolution } from '../types'
import { powProgress, searchNonce } from './core'
import type { PowWorkerReply } from './worker'

export interface SolveOptions {
  /** 0..0.99 estimate, called often. */
  onProgress?: (p: number) => void
  signal?: AbortSignal
  /** Skip the Web Worker (tests / CSP-restricted environments). */
  noWorker?: boolean
}

export class PowAbortError extends Error {
  constructor() {
    super('pow aborted')
    this.name = 'PowAbortError'
  }
}

function solveInWorker(c: PowChallenge, o: SolveOptions): Promise<number> | null {
  if (o.noWorker || typeof Worker === 'undefined') return null
  let w: Worker
  try {
    w = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' })
  } catch {
    return null
  }
  return new Promise<number>((resolve, reject) => {
    const stop = () => w.terminate()
    o.signal?.addEventListener('abort', () => {
      stop()
      reject(new PowAbortError())
    })
    w.onmessage = (e: MessageEvent<PowWorkerReply>) => {
      const m = e.data
      if (m.type === 'progress') o.onProgress?.(powProgress(m.tried, c.bits))
      else {
        stop()
        if (m.nonce === null) reject(new Error('pow not found'))
        else resolve(m.nonce)
      }
    }
    // worker failed to load (CSP / bundler): signal "fall back"
    w.onerror = () => {
      stop()
      reject(new WorkerUnavailable())
    }
    w.postMessage({ challenge: c.challenge, bits: c.bits })
  })
}

class WorkerUnavailable extends Error {}

/** Solve a challenge: Web Worker first, chunked main-thread loop as a fallback. */
export async function solvePow(c: PowChallenge, o: SolveOptions = {}): Promise<PowSolution> {
  if (o.signal?.aborted) throw new PowAbortError()
  const viaWorker = solveInWorker(c, o)
  if (viaWorker) {
    try {
      const nonce = await viaWorker
      o.onProgress?.(1)
      return { challenge: c.challenge, nonce: String(nonce) }
    } catch (e) {
      if (!(e instanceof WorkerUnavailable)) throw e
    }
  }
  const nonce = await searchNonce({
    challenge: c.challenge,
    bits: c.bits,
    batch: 2048,
    forceSync: true,
    onBatch: async (tried) => {
      if (o.signal?.aborted) return false
      o.onProgress?.(powProgress(tried, c.bits))
      await new Promise((r) => setTimeout(r, 0))
    },
  })
  if (o.signal?.aborted) throw new PowAbortError()
  if (nonce === null) throw new Error('pow not found')
  o.onProgress?.(1)
  return { challenge: c.challenge, nonce: String(nonce) }
}

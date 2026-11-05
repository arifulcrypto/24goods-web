import { searchNonce } from './core'

export interface PowWorkerRequest {
  challenge: string
  bits: number
}
export type PowWorkerReply = { type: 'progress'; tried: number } | { type: 'done'; nonce: number | null }

const ctx = self as unknown as {
  onmessage: ((e: MessageEvent<PowWorkerRequest>) => void) | null
  postMessage(m: PowWorkerReply): void
}

ctx.onmessage = (e) => {
  const { challenge, bits } = e.data
  void searchNonce({
    challenge,
    bits,
    forceSync: true,
    batch: 8192,
    onBatch: (tried) => ctx.postMessage({ type: 'progress', tried }),
  }).then((nonce) => ctx.postMessage({ type: 'done', nonce }))
}

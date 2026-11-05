/**
 * Voice recorder helper built on MediaRecorder.
 * Prefers audio/webm;codecs=opus (Chrome/Firefox/Android), falls back to audio/mp4 (iOS Safari).
 * Max 60 s (contract: duration_ms ≤ 60000, ≤ 1 MB) — auto-stops at the limit.
 */

export const VOICE_MAX_MS = 60_000
export const VOICE_MIN_MS = 800
const CANDIDATE_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/mpeg']

export interface VoiceResult {
  blob: Blob
  duration_ms: number
  mime: string
}

export type VoiceErrorCode = 'unsupported' | 'permission_denied' | 'no_device' | 'too_short' | 'failed'
export class VoiceError extends Error {
  constructor(readonly code: VoiceErrorCode, message?: string) {
    super(message ?? code)
    this.name = 'VoiceError'
  }
}

export function pickVoiceMimeType(isTypeSupported?: (t: string) => boolean): string | null {
  const check =
    isTypeSupported ??
    ((t: string) =>
      typeof MediaRecorder !== 'undefined' && typeof MediaRecorder.isTypeSupported === 'function'
        ? MediaRecorder.isTypeSupported(t)
        : false)
  for (const t of CANDIDATE_TYPES) if (check(t)) return t
  return null
}

export function isVoiceSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof MediaRecorder !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia
  )
}

/** File extension for a recorded mime type (used as the upload filename). */
export function voiceExtension(mime: string): string {
  if (mime.includes('webm')) return 'webm'
  if (mime.includes('mp4') || mime.includes('aac')) return 'm4a'
  if (mime.includes('ogg')) return 'ogg'
  if (mime.includes('mpeg')) return 'mp3'
  return 'bin'
}

export interface VoiceRecorderOptions {
  maxMs?: number
  /** Called ~10×/s with elapsed ms while recording. */
  onTick?: (elapsedMs: number) => void
  /** Called when the max duration is reached (recording stops; `stop()` promise resolves). */
  onMaxReached?: () => void
}

export class VoiceRecorder {
  private rec: MediaRecorder | null = null
  private stream: MediaStream | null = null
  private chunks: Blob[] = []
  private startedAt = 0
  private tick: ReturnType<typeof setInterval> | null = null
  private maxTimer: ReturnType<typeof setTimeout> | null = null
  private stopPromise: Promise<VoiceResult> | null = null
  private cancelled = false
  private readonly maxMs: number

  constructor(private readonly opts: VoiceRecorderOptions = {}) {
    this.maxMs = Math.min(opts.maxMs ?? VOICE_MAX_MS, VOICE_MAX_MS)
  }

  get recording(): boolean {
    return this.rec?.state === 'recording'
  }
  get elapsedMs(): number {
    return this.startedAt ? Date.now() - this.startedAt : 0
  }

  async start(): Promise<void> {
    if (!isVoiceSupported()) throw new VoiceError('unsupported')
    if (this.rec) return
    const mime = pickVoiceMimeType()
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      })
    } catch (e) {
      const name = (e as { name?: string })?.name
      if (name === 'NotAllowedError' || name === 'SecurityError') throw new VoiceError('permission_denied')
      if (name === 'NotFoundError' || name === 'OverconstrainedError') throw new VoiceError('no_device')
      throw new VoiceError('failed')
    }
    this.chunks = []
    this.cancelled = false
    const rec = new MediaRecorder(this.stream, mime ? { mimeType: mime, audioBitsPerSecond: 32000 } : undefined)
    this.rec = rec
    let resolve!: (r: VoiceResult) => void
    let reject!: (e: unknown) => void
    this.stopPromise = new Promise<VoiceResult>((res, rej) => {
      resolve = res
      reject = rej
    })
    // Avoid unhandled rejection if nobody awaits (e.g. cancel()).
    this.stopPromise.catch(() => undefined)
    rec.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) this.chunks.push(ev.data)
    }
    rec.onstop = () => {
      const duration = Math.min(this.maxMs, Date.now() - this.startedAt)
      const type = rec.mimeType || mime || 'audio/webm'
      const blob = new Blob(this.chunks, { type: type.split(';')[0] })
      this.cleanup()
      if (this.cancelled) reject(new VoiceError('failed', 'cancelled'))
      else if (duration < VOICE_MIN_MS) reject(new VoiceError('too_short'))
      else resolve({ blob, duration_ms: duration, mime: blob.type })
    }
    rec.onerror = () => {
      this.cleanup()
      reject(new VoiceError('failed'))
    }
    rec.start(250)
    this.startedAt = Date.now()
    this.tick = setInterval(() => this.opts.onTick?.(this.elapsedMs), 100)
    this.maxTimer = setTimeout(() => {
      this.opts.onMaxReached?.()
      if (this.rec?.state === 'recording') this.rec.stop()
    }, this.maxMs)
  }

  /** Stop and get the recording. Rejects with VoiceError('too_short') for < 0.8 s. */
  stop(): Promise<VoiceResult> {
    const p = this.stopPromise
    if (!p) return Promise.reject(new VoiceError('failed', 'not recording'))
    if (this.rec?.state === 'recording') this.rec.stop()
    return p
  }

  cancel(): void {
    this.cancelled = true
    if (this.rec?.state === 'recording') this.rec.stop()
    else this.cleanup()
  }

  private cleanup(): void {
    if (this.tick) clearInterval(this.tick)
    if (this.maxTimer) clearTimeout(this.maxTimer)
    this.tick = null
    this.maxTimer = null
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
    this.rec = null
    this.startedAt = 0
  }
}

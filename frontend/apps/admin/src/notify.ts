/** CS alert primitives: a short chime (WebAudio, no asset file) and browser Notifications. */
let ctx: AudioContext | null = null

/** Two-tone chime. Silently does nothing when audio is blocked (autoplay policy etc.). */
export function beep(): void {
  try {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AC) return
    ctx ??= new AC()
    if (ctx.state === 'suspended') void ctx.resume().catch(() => undefined)
    const tone = (freq: number, at: number) => {
      const o = ctx!.createOscillator()
      const g = ctx!.createGain()
      o.type = 'sine'
      o.frequency.value = freq
      g.gain.setValueAtTime(0.0001, at)
      g.gain.exponentialRampToValueAtTime(0.15, at + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, at + 0.22)
      o.connect(g).connect(ctx!.destination)
      o.start(at)
      o.stop(at + 0.24)
    }
    tone(880, ctx.currentTime)
    tone(1320, ctx.currentTime + 0.14)
  } catch {
    /* autoplay policy etc. */
  }
}

export type PermissionState = NotificationPermission | 'unsupported'

export function notificationPermission(): PermissionState {
  if (typeof Notification === 'undefined') return 'unsupported'
  return Notification.permission
}

export async function ensureNotificationPermission(): Promise<boolean> {
  if (typeof Notification === 'undefined') return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  try {
    return (await Notification.requestPermission()) === 'granted'
  } catch {
    return false
  }
}

/** OS notification. Bodies are truncated; `tag` collapses repeated alerts of the same kind. */
export function desktopNotify(title: string, body: string, tag: string, onClick?: () => void): void {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  try {
    const n = new Notification(title, { body: body.slice(0, 120), tag: `kesiv-${tag}`, silent: true })
    n.onclick = () => {
      try {
        window.focus()
      } catch {
        /* ignore */
      }
      onClick?.()
      n.close()
    }
  } catch {
    /* ignore (e.g. Android Chrome requires a service worker) */
  }
}

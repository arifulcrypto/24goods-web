/** Alert primitives shared by the buyer app and the console: a short chime (WebAudio, no asset
 *  file) and browser Notifications (through a notification-only service worker when available). */
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

let swReg: ServiceWorkerRegistration | null = null
let clickHandler: ((url: string) => void) | null = null

/** v11: register the notification-only service worker (same origin). Safe to call
 *  repeatedly; does nothing where service workers are unavailable (http dev hosts, old browsers). */
export interface NotifyWorkerOptions {
  /** Worker script URL (same origin), e.g. `/sw.js` or `/admin/sw.js`. */
  script: string
  scope: string
  /** Default icon for notifications. */
  icon: string
}
let iconUrl = '/favicon.svg'

export async function registerNotifyWorker(onClick: (url: string) => void, o: NotifyWorkerOptions = { script: '/sw.js', scope: '/', icon: '/favicon.svg' }): Promise<void> {
  clickHandler = onClick
  iconUrl = o.icon
  try {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return
    swReg = await navigator.serviceWorker.register(o.script, { scope: o.scope })
    navigator.serviceWorker.addEventListener('message', (e: MessageEvent) => {
      const d = e.data as { type?: string; url?: string } | null
      if (d?.type === 'kesiv-notification-click' && typeof d.url === 'string') clickHandler?.(d.url)
    })
  } catch {
    swReg = null
  }
}

/** Browsers suspend an AudioContext created without a user gesture; resume it on the first one so
 *  the chime also plays later while the tab is in the background. */
export function unlockAudioOnFirstGesture(): void {
  const unlock = () => {
    try {
      const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (AC) {
        ctx ??= new AC()
        if (ctx.state === 'suspended') void ctx.resume().catch(() => undefined)
      }
    } catch {
      /* ignore */
    }
    window.removeEventListener('pointerdown', unlock)
    window.removeEventListener('keydown', unlock)
  }
  window.addEventListener('pointerdown', unlock, { once: true })
  window.addEventListener('keydown', unlock, { once: true })
}

export interface DesktopNotifyOptions {
  /** Same-origin path to open on click. */
  url?: string
  /** Stays on screen until dismissed (urgent alerts). */
  urgent?: boolean
}

/** OS notification. Bodies are truncated; `tag` collapses repeated alerts of the same kind. Uses the
 *  service worker registration when present (works minimised / on Android), else `new Notification`. */
export function desktopNotify(title: string, body: string, tag: string, onClick?: () => void, opts: DesktopNotifyOptions = {}): void {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  const options: NotificationOptions & { requireInteraction?: boolean; renotify?: boolean } = {
    body: body.slice(0, 120),
    tag: `kesiv-${tag}`,
    silent: true, // the console plays its own chime
    icon: iconUrl,
    requireInteraction: !!opts.urgent,
    renotify: true,
    data: { url: opts.url ?? '/' },
  }
  if (swReg) {
    void swReg.showNotification(title, options).catch(() => pageNotify(title, options, onClick))
    return
  }
  pageNotify(title, options, onClick)
}

function pageNotify(title: string, options: NotificationOptions, onClick?: () => void): void {
  try {
    const n = new Notification(title, options)
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

/** `(3) Title` — unread count in the tab title (99+ cap). */
export function titleWithCount(base: string, count: number): string {
  if (count <= 0) return base
  return `(${count > 99 ? '99+' : count}) ${base}`
}

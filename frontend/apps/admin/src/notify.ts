/** CS alert primitives. The implementation lives in the shared package (the buyer app reuses it);
 *  this wrapper only pins the console's service worker + icon. */
import { registerNotifyWorker as register } from '@24goods/shared'

export {
  beep,
  desktopNotify,
  ensureNotificationPermission,
  notificationPermission,
  unlockAudioOnFirstGesture,
} from '@24goods/shared'
export type { DesktopNotifyOptions, PermissionState } from '@24goods/shared'

export function registerNotifyWorker(onClick: (url: string) => void): Promise<void> {
  return register(onClick, { script: '/admin/sw.js', scope: '/admin/', icon: '/admin/favicon.svg' })
}

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ensureNotificationPermission, notificationPermission } from '@24goods/shared'
import { useSiteStore } from './site'

const KEY = '24goods.buyer.notify'
interface Local {
  sound?: boolean
  desktop?: boolean
}
function read(): Local {
  try {
    const j = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Local
    return {
      ...(typeof j.sound === 'boolean' ? { sound: j.sound } : {}),
      ...(typeof j.desktop === 'boolean' ? { desktop: j.desktop } : {}),
    }
  } catch {
    return {}
  }
}

/** Buyer chat alerts: the buyer's own choice (localStorage) wins over the store defaults
 *  (`/site` → `notify_defaults.buyer`, set in admin Settings → Notifications). */
export const useNotifyPrefsStore = defineStore('notifyPrefs', () => {
  const site = useSiteStore()
  const local = ref<Local>(read())
  const permission = ref(notificationPermission())
  function persist() {
    try {
      localStorage.setItem(KEY, JSON.stringify(local.value))
    } catch {
      /* private mode: kept for this session only */
    }
  }
  const sound = computed(() => local.value.sound ?? site.site?.notify_defaults?.buyer?.sound ?? true)
  /** Desktop alerts need both the preference and the browser permission. */
  const wantsDesktop = computed(() => local.value.desktop ?? site.site?.notify_defaults?.buyer?.desktop ?? false)
  const desktop = computed(() => wantsDesktop.value && permission.value === 'granted')
  function setSound(v: boolean) {
    local.value = { ...local.value, sound: v }
    persist()
  }
  /** Enabling asks for the browser permission; returns false when it was not granted. */
  async function setDesktop(v: boolean): Promise<boolean> {
    if (v) {
      const ok = await ensureNotificationPermission()
      permission.value = notificationPermission()
      local.value = { ...local.value, desktop: ok }
      persist()
      return ok
    }
    local.value = { ...local.value, desktop: false }
    persist()
    return true
  }
  return { sound, desktop, wantsDesktop, permission, setSound, setDesktop }
})

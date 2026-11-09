import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { mergeOrderAlerts, type OrderAlertPrefs } from '../lib/notifyPolicy'

const KEY = '24goods.admin.prefs'
interface Prefs {
  notify: boolean
  sound: boolean
  collapsed: boolean
  orderAlerts: OrderAlertPrefs
}
function load(): Prefs {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      const j = JSON.parse(raw) as Partial<Prefs>
      return { notify: false, sound: true, collapsed: false, ...j, orderAlerts: mergeOrderAlerts(j.orderAlerts) }
    }
  } catch {
    /* ignore */
  }
  return { notify: false, sound: true, collapsed: false, orderAlerts: mergeOrderAlerts(null) }
}

export const usePrefsStore = defineStore('prefs', () => {
  const p = load()
  const notify = ref(p.notify)
  const sound = ref(p.sound)
  const collapsed = ref(p.collapsed)
  const orderAlerts = ref<OrderAlertPrefs>(p.orderAlerts)
  watch([notify, sound, collapsed, orderAlerts], () => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ notify: notify.value, sound: sound.value, collapsed: collapsed.value, orderAlerts: orderAlerts.value }))
    } catch {
      /* ignore */
    }
  }, { deep: true })
  return { notify, sound, collapsed, orderAlerts }
})

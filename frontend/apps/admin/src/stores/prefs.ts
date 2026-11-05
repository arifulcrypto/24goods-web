import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const KEY = '24goods.admin.prefs'
interface Prefs {
  notify: boolean
  sound: boolean
  collapsed: boolean
}
function load(): Prefs {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { notify: false, sound: true, collapsed: false, ...(JSON.parse(raw) as Partial<Prefs>) }
  } catch {
    /* ignore */
  }
  return { notify: false, sound: true, collapsed: false }
}

export const usePrefsStore = defineStore('prefs', () => {
  const p = load()
  const notify = ref(p.notify)
  const sound = ref(p.sound)
  const collapsed = ref(p.collapsed)
  watch([notify, sound, collapsed], () => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ notify: notify.value, sound: sound.value, collapsed: collapsed.value }))
    } catch {
      /* ignore */
    }
  })
  return { notify, sound, collapsed }
})

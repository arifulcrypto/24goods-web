import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { AnnouncementItem } from '@24goods/shared'
import { api } from '../api'
import { useAuthStore } from './auth'
import { useSiteStore } from './site'

const READ_KEY = 'kesiv.ann.read'
const PROMPTED_KEY = 'kesiv.ann.prompted'

function readSet(key: string): Set<string> | null {
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return null
    const j = JSON.parse(raw) as unknown
    return new Set(Array.isArray(j) ? j.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return null
  }
}
function writeSet(key: string, s: Set<string>) {
  try {
    localStorage.setItem(key, JSON.stringify([...s].slice(-200)))
  } catch {
    /* private mode: kept for this page view only */
  }
}

/**
 * Announcements (news): unread badge + one-time prompt. Signed-in users keep read state on the
 * server; guests keep it in localStorage (first visit = everything already published counts as read).
 */
export const useAnnouncementsStore = defineStore('announcements', () => {
  const site = useSiteStore()
  const auth = useAuthStore()
  const items = ref<AnnouncementItem[]>([])
  const serverUnread = ref<number | null>(null)
  const guestRead = ref<Set<string>>(new Set())
  const loaded = ref(false)

  const cfg = computed(() => site.site?.announcements)
  const showBadge = computed(() => cfg.value?.show_badge ?? true)
  const promptNew = computed(() => cfg.value?.prompt_new ?? true)

  function isRead(a: AnnouncementItem): boolean {
    return auth.loggedIn ? a.read === true : guestRead.value.has(a.id)
  }
  const unreadItems = computed(() => items.value.filter((a) => !isRead(a)))
  const unread = computed(() => (auth.loggedIn && serverUnread.value !== null ? serverUnread.value : unreadItems.value.length))
  const newestUnread = computed(() => {
    const list = [...unreadItems.value].sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? ''))
    return list[0] ?? null
  })

  async function refresh(): Promise<void> {
    try {
      const r = await api.announcements.list({ page: 1, page_size: 50 })
      items.value = r.items
      serverUnread.value = r.unread
      if (!auth.loggedIn) {
        const stored = readSet(READ_KEY)
        if (stored === null) {
          // first visit: do not flood a new guest with the whole archive
          guestRead.value = new Set(r.items.map((a) => a.id))
          writeSet(READ_KEY, guestRead.value)
        } else guestRead.value = stored
      }
      loaded.value = true
    } catch {
      /* the bell simply stays quiet */
    }
  }
  async function markRead(id: string): Promise<void> {
    if (auth.loggedIn) {
      const it = items.value.find((a) => a.id === id)
      if (it && it.read !== true) {
        it.read = true
        if (serverUnread.value !== null) serverUnread.value = Math.max(0, serverUnread.value - 1)
      }
      try {
        await api.announcements.markRead(id)
      } catch {
        void refresh()
      }
    } else {
      guestRead.value = new Set(guestRead.value).add(id)
      writeSet(READ_KEY, guestRead.value)
    }
  }
  async function markAll(): Promise<void> {
    if (auth.loggedIn) {
      items.value.forEach((a) => (a.read = true))
      serverUnread.value = 0
      try {
        await api.announcements.markAllRead()
      } catch {
        void refresh()
      }
    } else {
      guestRead.value = new Set(items.value.map((a) => a.id))
      writeSet(READ_KEY, guestRead.value)
    }
  }
  /** Each announcement is prompted at most once per browser ("Later" counts). */
  function prompted(id: string): boolean {
    return readSet(PROMPTED_KEY)?.has(id) ?? false
  }
  function markPrompted(id: string) {
    writeSet(PROMPTED_KEY, (readSet(PROMPTED_KEY) ?? new Set()).add(id))
  }
  function reset() {
    items.value = []
    serverUnread.value = null
    loaded.value = false
  }

  return { items, unread, unreadItems, newestUnread, showBadge, promptNew, loaded, isRead, refresh, markRead, markAll, prompted, markPrompted, reset }
})

/** Recently viewed products + last looked-up order numbers (localStorage, always try/catch). */
export const RECENT_KEY = 'kesiv.recent'
export const RECENT_MAX = 20
export const LOOKUPS_KEY = 'kesiv.lookups'
export const LOOKUPS_MAX = 5

export interface RecentEntry {
  id: string
  at: number
}
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function store(): Store | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

function readList<T>(key: string, s: Store | null, ok: (x: unknown) => x is T): T[] {
  try {
    const raw = s?.getItem(key)
    if (!raw) return []
    const v: unknown = JSON.parse(raw)
    return Array.isArray(v) ? v.filter(ok) : []
  } catch {
    return []
  }
}
function writeList(key: string, s: Store | null, v: unknown[]): void {
  try {
    s?.setItem(key, JSON.stringify(v))
  } catch {
    /* storage blocked / full */
  }
}

const isEntry = (x: unknown): x is RecentEntry =>
  !!x && typeof x === 'object' && typeof (x as RecentEntry).id === 'string' && typeof (x as RecentEntry).at === 'number'

export function getRecent(s: Store | null = store()): RecentEntry[] {
  return readList(RECENT_KEY, s, isEntry).slice(0, RECENT_MAX)
}
export function recentIds(s: Store | null = store()): string[] {
  return getRecent(s).map((e) => e.id)
}
/** Move `id` to the front (newest first), dedupe, cap at 20. */
export function addRecent(id: string, s: Store | null = store(), now: number = Date.now()): RecentEntry[] {
  if (!id) return getRecent(s)
  const next = [{ id, at: now }, ...getRecent(s).filter((e) => e.id !== id)].slice(0, RECENT_MAX)
  writeList(RECENT_KEY, s, next)
  return next
}
export function clearRecent(s: Store | null = store()): void {
  try {
    s?.removeItem(RECENT_KEY)
  } catch {
    /* ignore */
  }
}

const isStr = (x: unknown): x is string => typeof x === 'string'
export function getLookups(s: Store | null = store()): string[] {
  return readList(LOOKUPS_KEY, s, isStr).slice(0, LOOKUPS_MAX)
}
/** Remember an order number (never the access code). */
export function addLookup(orderNo: string, s: Store | null = store()): string[] {
  const no = orderNo.trim()
  if (!no) return getLookups(s)
  const next = [no, ...getLookups(s).filter((x) => x !== no)].slice(0, LOOKUPS_MAX)
  writeList(LOOKUPS_KEY, s, next)
  return next
}

/** Announcement dismissal, keyed by version. */
export const ANNOUNCE_KEY = 'kesiv.announce'
export function isAnnouncementDismissed(version: string | number, s: Store | null = store()): boolean {
  try {
    return s?.getItem(ANNOUNCE_KEY) === String(version)
  } catch {
    return false
  }
}
export function dismissAnnouncement(version: string | number, s: Store | null = store()): void {
  try {
    s?.setItem(ANNOUNCE_KEY, String(version))
  } catch {
    /* ignore */
  }
}

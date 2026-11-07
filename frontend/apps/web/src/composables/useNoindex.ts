import { onBeforeUnmount, toValue, watchEffect, type MaybeRefOrGetter } from 'vue'

/**
 * Sets `<meta name="robots" content="noindex,nofollow">` while `active` is true (404 pages) and
 * restores the previous value when it turns false or the page unmounts (review-v5.2 §6).
 */
export function useNoindex(active: MaybeRefOrGetter<boolean> = true): void {
  let el: HTMLMetaElement | null = null
  let prev: string | null = null
  let created = false
  const restore = () => {
    if (!el) return
    if (created) el.remove()
    else if (prev === null) el.removeAttribute('content')
    else el.content = prev
    el = null
    created = false
  }
  watchEffect(() => {
    if (!toValue(active)) return restore()
    if (el) return
    el = document.head.querySelector<HTMLMetaElement>('meta[name="robots"]')
    if (el) prev = el.getAttribute('content')
    else {
      el = document.createElement('meta')
      el.name = 'robots'
      document.head.appendChild(el)
      created = true
    }
    el.content = 'noindex,nofollow'
  })
  onBeforeUnmount(restore)
}

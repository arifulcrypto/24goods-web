/* Buyer-app service worker (v11): notifications only. No fetch handler and no caching — the app
   always loads from the network. Showing notifications through a registration keeps them working
   while the browser is minimised and on Android Chrome (where `new Notification` throws). */
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const data = event.notification.data || {}
  // same-origin absolute paths only (never `//host`, `/\host` or a scheme)
  const safe = (u) => {
    if (typeof u !== 'string' || !/^\/(?![/\\])/.test(u)) return false
    try {
      return new URL(u, self.location.origin).origin === self.location.origin
    } catch {
      return false
    }
  }
  const url = safe(data.url) ? data.url : '/'
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const client of all) {
        if (!new URL(client.url).pathname.startsWith('/admin/')) {
          await client.focus()
          client.postMessage({ type: 'kesiv-notification-click', url })
          return
        }
      }
      await self.clients.openWindow(url)
    })(),
  )
})

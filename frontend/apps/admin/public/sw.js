/* Kesiv console service worker (v11): notifications only. No fetch handler, no caching — the
   console always loads from the network. Showing notifications through a registration keeps them
   working while the browser is minimised and on Android Chrome (where `new Notification` throws). */
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const data = event.notification.data || {}
  const url = typeof data.url === 'string' && data.url.startsWith('/admin/') ? data.url : '/admin/'
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const client of all) {
        if (new URL(client.url).pathname.startsWith('/admin/')) {
          await client.focus()
          client.postMessage({ type: 'kesiv-notification-click', url })
          return
        }
      }
      await self.clients.openWindow(url)
    })(),
  )
})

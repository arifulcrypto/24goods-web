import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { safeRedirect } from '@24goods/shared'
import { useAuthStore } from './stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** Requires a signed-in user. */
    auth?: boolean
    /** Show the bottom tabbar. */
    tab?: 'home' | 'messages' | 'orders' | 'me'
    /** Use the wider 960 px column (Home / Search grids). */
    wide?: boolean
    /** Signed-in users are bounced away (login/register). */
    guestOnly?: boolean
  }
}

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: () => import('./pages/HomePage.vue'), meta: { tab: 'home', wide: true } },
  { path: '/search', name: 'search', component: () => import('./pages/SearchPage.vue'), meta: { wide: true } },
  { path: '/p/:id', name: 'product', component: () => import('./pages/ProductPage.vue') },
  { path: '/buy/:id', name: 'buy', component: () => import('./pages/ProductPage.vue') },
  { path: '/lookup', name: 'lookup', component: () => import('./pages/LookupPage.vue') },
  { path: '/help', name: 'help', component: () => import('./pages/HelpPage.vue') },
  { path: '/a/:slug', name: 'article', component: () => import('./pages/ArticlePage.vue') },
  { path: '/chat', name: 'chat', component: () => import('./pages/ChatPage.vue'), meta: { auth: true } },
  { path: '/messages', name: 'messages', component: () => import('./pages/MessagesPage.vue'), meta: { auth: true, tab: 'messages' } },
  { path: '/orders', name: 'orders', component: () => import('./pages/OrdersPage.vue'), meta: { auth: true, tab: 'orders' } },
  { path: '/orders/:id', name: 'order', component: () => import('./pages/OrderDetailPage.vue'), meta: { auth: true } },
  { path: '/me', name: 'me', component: () => import('./pages/MePage.vue'), meta: { tab: 'me' } },
  { path: '/login', name: 'login', component: () => import('./pages/LoginPage.vue'), meta: { guestOnly: true } },
  { path: '/register', name: 'register', component: () => import('./pages/RegisterPage.vue'), meta: { guestOnly: true } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./pages/NotFoundPage.vue') },
]

export const router = createRouter({
  history: createWebHistory('/'),
  routes,
  scrollBehavior(_to, _from, saved) {
    return saved ?? { top: 0 }
  },
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.init()
  if (to.meta.auth && !auth.loggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && auth.loggedIn) {
    return safeRedirect(to.query.redirect, '/me')
  }
  return true
})

/** Global 401: drop the session and send the user to /login if the page needs auth. */
export function handleUnauthorized(): void {
  const auth = useAuthStore()
  auth.clear()
  const cur = router.currentRoute.value
  if (cur.meta.auth) void router.replace({ name: 'login', query: { redirect: cur.fullPath } })
}

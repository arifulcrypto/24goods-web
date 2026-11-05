import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import type { Role } from '@24goods/shared'
import { safeRedirect } from '@24goods/shared'
import { useAuthStore } from './stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    roles?: Role[]
    titleKey?: string
  }
}

const STAFF: Role[] = ['agent', 'admin']
const ADMIN: Role[] = ['admin']

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('./pages/LoginPage.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('./layouts/AdminLayout.vue'),
    meta: { roles: STAFF },
    children: [
      { path: '', name: 'dashboard', component: () => import('./pages/DashboardPage.vue'), meta: { roles: STAFF, titleKey: 'nav.dashboard' } },
      { path: 'inbox', name: 'inbox', component: () => import('./pages/InboxPage.vue'), meta: { roles: STAFF, titleKey: 'nav.inbox' } },
      { path: 'orders', name: 'orders', component: () => import('./pages/OrdersPage.vue'), meta: { roles: STAFF, titleKey: 'nav.orders' } },
      { path: 'products', name: 'products', component: () => import('./pages/ProductsPage.vue'), meta: { roles: STAFF, titleKey: 'nav.products' } },
      { path: 'inventory', name: 'inventory', component: () => import('./pages/InventoryPage.vue'), meta: { roles: ADMIN, titleKey: 'nav.inventory' } },
      { path: 'ledger', name: 'ledger', component: () => import('./pages/LedgerPage.vue'), meta: { roles: ADMIN, titleKey: 'nav.ledger' } },
      { path: 'audit', name: 'audit', component: () => import('./pages/AuditPage.vue'), meta: { roles: ADMIN, titleKey: 'nav.audit' } },
      { path: 'forbidden', name: 'forbidden', component: () => import('./pages/ForbiddenPage.vue'), meta: { roles: STAFF } },
      { path: ':pathMatch(.*)*', name: 'not-found', component: () => import('./pages/NotFoundPage.vue'), meta: { roles: STAFF } },
    ],
  },
]

export const router = createRouter({ history: createWebHistory('/admin/'), routes })

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.init()
  if (to.meta.public) {
    if (to.name === 'login' && auth.isStaff) return safeRedirect(to.query.redirect, '/')
    return true
  }
  if (!auth.user || !auth.isStaff) return { name: 'login', query: { redirect: to.fullPath } }
  const roles = to.meta.roles
  if (roles && !roles.includes(auth.user.role)) return { name: 'forbidden' }
  return true
})

export function handleUnauthorized(): void {
  const auth = useAuthStore()
  auth.clear()
  const cur = router.currentRoute.value
  if (!cur.meta.public) void router.replace({ name: 'login', query: { redirect: cur.fullPath } })
}

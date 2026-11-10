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
      { path: 'operations', name: 'operations', component: () => import('./pages/OperationsPage.vue'), meta: {roles: ADMIN, titleKey: 'ops.title'} },
      { path: 'services/:id?', name: 'services', component: () => import('./pages/ServicesPage.vue'), meta: {roles: STAFF, titleKey: 'services.title'} },
      { path: '', name: 'dashboard', component: () => import('./pages/DashboardPage.vue'), meta: { roles: STAFF, titleKey: 'nav.dashboard' } },
      { path: 'inbox', name: 'inbox', component: () => import('./pages/InboxPage.vue'), meta: { roles: STAFF, titleKey: 'nav.inbox' } },
      { path: 'orders', name: 'orders', component: () => import('./pages/OrdersPage.vue'), meta: { roles: STAFF, titleKey: 'nav.orders' } },
      { path: 'users', name: 'users', component: () => import('./pages/UsersPage.vue'), meta: { roles: STAFF, titleKey: 'v5.nav.users' } },
      { path: 'after-sales', name: 'after-sales', component: () => import('./pages/AfterSalesPage.vue'), meta: { roles: STAFF, titleKey: 'nav.afterSales' } },
      { path: 'announcements', name: 'announcements', component: () => import('./pages/AnnouncementsPage.vue'), meta: { roles: STAFF, titleKey: 'news.title' } },
      { path: 'articles', name: 'articles', component: () => import('./pages/ArticlesPage.vue'), meta: { roles: ADMIN, titleKey: 'nav.articles' } },
      { path: 'site', redirect: '/settings' },
      { path: 'settings/:tab?', name: 'settings', component: () => import('./pages/SettingsPage.vue'), meta: { roles: ADMIN, titleKey: 'v5.nav.settings' } },
      { path: 'categories', name: 'categories', component: () => import('./pages/CategoriesPage.vue'), meta: { roles: ADMIN, titleKey: 'v5.nav.categories' } },
      { path: 'promo-codes', name: 'promo-codes', component: () => import('./pages/PromoCodesPage.vue'), meta: { roles: ADMIN, titleKey: 'v5.nav.promos' } },
      { path: 'resellers', name: 'resellers', component: () => import('./pages/ResellersPage.vue'), meta: { roles: ADMIN, titleKey: 'v5.nav.resellers' } },
      { path: 'payouts', name: 'payouts', component: () => import('./pages/PayoutsPage.vue'), meta: { roles: ADMIN, titleKey: 'v5.nav.payouts' } },
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

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ChatDotRound,
  Coin,
  DataBoard,
  Discount,
  Document,
  Expand,
  Fold,
  Goods,
  List,
  Menu,
  Reading,
  Service,
  Setting,
  Shop,
  Tickets,
  User,
  Wallet,
} from '@element-plus/icons-vue'
import { LogoMark } from '@24goods/shared/ui'
import NotifyPopover from '../components/NotifyPopover.vue'
import { useCsNotifications } from '../composables/useCsNotifications'
import { useDeskStore } from '../stores/desk'
import AgentStatusSelect from '../components/AgentStatusSelect.vue'
import { useAuthStore } from '../stores/auth'
import { useRealtimeStore } from '../stores/realtime'
import { usePrefsStore } from '../stores/prefs'
import { setLocale } from '../locale'
import type { AppLocale } from '../i18n'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const rt = useRealtimeStore()
const prefs = usePrefsStore()
const desk = useDeskStore()
useCsNotifications()
const narrow = ref(false)

interface MenuItem {
  path: string
  key: string
  icon: unknown
}
const groups = computed(() => {
  const g: { key: string; items: MenuItem[] }[] = [
    {
      key: 'v5.nav.g.operations',
      items: [
        { path: '/', key: 'nav.dashboard', icon: DataBoard },
        { path: '/inbox', key: 'nav.inbox', icon: ChatDotRound },
        { path: '/orders', key: 'nav.orders', icon: List },
        { path: '/after-sales', key: 'nav.afterSales', icon: Service },
        { path: '/users', key: 'v5.nav.users', icon: User },
      ],
    },
    {
      key: 'v5.nav.g.catalog',
      items: [
        { path: '/products', key: 'nav.products', icon: Goods },
        ...(auth.isAdmin
          ? [
              { path: '/categories', key: 'v5.nav.categories', icon: Menu },
              { path: '/inventory', key: 'nav.inventory', icon: Tickets },
              { path: '/promo-codes', key: 'v5.nav.promos', icon: Discount },
            ]
          : []),
      ],
    },
  ]
  if (auth.isAdmin) {
    g.push(
      { key: 'v5.nav.g.content', items: [{ path: '/articles', key: 'nav.articles', icon: Reading }] },
      {
        key: 'v5.nav.g.resellers',
        items: [
          { path: '/resellers', key: 'v5.nav.resellers', icon: Shop },
          { path: '/payouts', key: 'v5.nav.payouts', icon: Wallet },
        ],
      },
      {
        key: 'v5.nav.g.system',
        items: [
          { path: '/settings', key: 'v5.nav.settings', icon: Setting },
          { path: '/audit', key: 'v5.nav.logs', icon: Document },
          { path: '/ledger', key: 'nav.ledger', icon: Coin },
        ],
      },
    )
  }
  return g
})
const active = computed(() => (route.path === '/' ? '/' : `/${route.path.split('/')[1]}`))
const collapsed = computed(() => prefs.collapsed || narrow.value)

function onResize() {
  narrow.value = window.innerWidth < 1100
}

onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
  rt.start()
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  rt.stop()
})

function switchLang(l: AppLocale) {
  setLocale(l)
}
function openShop() {
  window.open('/', '_blank', 'noopener')
}
async function logout() {
  rt.stop()
  await auth.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <el-aside :width="collapsed ? '64px' : '208px'" class="aside">
      <div class="logo">
        <LogoMark :size="32" :title="t('app.title')" />
        <span v-if="!collapsed" class="logo-text">{{ t('app.title') }}</span>
      </div>
      <el-menu :default-active="active" :collapse="collapsed" router class="menu" :collapse-transition="false">
        <el-menu-item-group v-for="g in groups" :key="g.key">
          <template v-if="!collapsed" #title>{{ t(g.key) }}</template>
          <el-menu-item v-for="m in g.items" :key="m.path" :index="m.path">
            <el-icon><component :is="m.icon" /></el-icon>
            <template #title>
              {{ t(m.key) }}
              <el-badge v-if="m.path === '/after-sales' && desk.afterSalesOpen > 0" :value="desk.afterSalesOpen" :max="99" class="nav-badge" />
            </template>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
      <button
        type="button"
        class="collapse-btn"
        :aria-label="collapsed ? t('nav.expand') : t('nav.collapse')"
        @click="prefs.collapsed = !prefs.collapsed"
      >
        <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
      </button>
    </el-aside>
    <el-container class="main-col">
      <el-header class="topbar" height="56px">
        <div class="top-left">
          <h1 class="top-title">{{ route.meta.titleKey ? t(route.meta.titleKey) : t('app.title') }}</h1>
          <el-tag
            size="small"
            :type="rt.state === 'open' ? 'success' : rt.state === 'polling' ? 'warning' : 'info'"
            effect="plain"
            round
          >
            {{ rt.state === 'open' ? t('top.live') : rt.state === 'polling' ? t('top.polling') : t('top.connecting') }}
          </el-tag>
        </div>
        <div class="top-right">
          <AgentStatusSelect />
          <NotifyPopover />
          <el-dropdown trigger="click" @command="switchLang">
            <el-button text>{{ locale === 'zh-CN' ? t('lang.zh') : t('lang.en') }}</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="en">{{ t('lang.en') }}</el-dropdown-item>
                <el-dropdown-item command="zh-CN">{{ t('lang.zh') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-dropdown trigger="click">
            <el-button text class="user-btn">
              <el-avatar :size="26" class="avatar">{{ (auth.user?.display_name || '?').charAt(0).toUpperCase() }}</el-avatar>
              <span class="user-name">{{ auth.user?.display_name }}</span>
            </el-button>
            <template #dropdown>
              <div class="user-menu">
                <div class="user-meta">
                  <div class="user-email">{{ auth.user?.email }}</div>
                  <el-tag size="small">{{ t(`top.role.${auth.user?.role ?? 'agent'}`) }}</el-tag>
                </div>
              </div>
              <el-dropdown-menu>
                <el-dropdown-item @click="openShop">{{ t('top.openShop') }}</el-dropdown-item>
                <el-dropdown-item divided @click="logout">{{ t('top.logout') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main">
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}
.aside {
  background: var(--a-sidebar);
  display: flex;
  flex-direction: column;
  transition: width 0.15s ease;
  overflow: hidden;
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 56px;
  padding: 0 16px;
  color: #fff;
}
.logo-text {
  font-weight: 700;
  white-space: nowrap;
}
.nav-badge {
  margin-left: 8px;
}
.menu {
  flex: 1;
  border-right: 0;
  --el-menu-bg-color: var(--a-sidebar);
  --el-menu-text-color: var(--a-sidebar-text);
  --el-menu-hover-bg-color: #1f2937;
  --el-menu-active-color: var(--a-sidebar-active);
  --el-menu-item-height: 44px;
  overflow-y: auto;
}
.menu :deep(.el-menu-item-group__title) {
  color: #6b7280;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 10px 20px 4px;
}
.menu:not(.el-menu--collapse) {
  width: 208px;
}
.collapse-btn {
  height: 44px;
  border: 0;
  background: transparent;
  color: var(--a-sidebar-text);
  cursor: pointer;
  font-size: 18px;
}
.main-col {
  min-width: 0;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--a-surface);
  border-bottom: 1px solid var(--a-line);
  padding: 0 16px;
}
.top-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.top-title {
  font-size: 16px;
  margin: 0;
  white-space: nowrap;
}
.top-right {
  display: flex;
  align-items: center;
  gap: 4px;
}
.user-btn {
  gap: 6px;
}
.avatar {
  background: #2563eb;
  font-size: 12px;
}
.user-name {
  margin-left: 6px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.user-menu {
  padding: 10px 16px 4px;
  min-width: 240px;
}
.user-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.user-email {
  font-size: 13px;
  overflow-wrap: anywhere;
}
.user-pref {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  min-height: 36px;
}
.main {
  padding: 0;
  background: var(--a-bg);
  overflow: auto;
}
@media (max-width: 900px) {
  .user-name,
  .top-title {
    display: none;
  }
}
</style>

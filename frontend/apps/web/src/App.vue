<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SecurityChallenge from './components/SecurityChallenge.vue'
import StorefrontGate from './components/StorefrontGate.vue'
import TabBar from './components/TabBar.vue'
import CsBubble from './components/CsBubble.vue'
import { accessBlock, safeAssetUrl } from '@24goods/shared'
import { AccessBlockedView } from '@24goods/shared/ui'
import { useAuthStore } from './stores/auth'
import { useRealtimeStore } from './stores/realtime'
import { useConversationsStore } from './stores/conversations'
import { useSupportStore } from './stores/support'
import { useSiteStore } from './stores/site'

const route = useRoute()
const { t } = useI18n()
const auth = useAuthStore()
const rt = useRealtimeStore()
const convs = useConversationsStore()
const support = useSupportStore()
const site = useSiteStore()

// v5 §4/§7: document title = storefront name / Settings → General name / brand.ts
// White-label storefronts (owner rule): no platform brand in title, meta tags or favicon.
// Server-rendered SEO pages carry a per-page <title>: keep it until the first in-app navigation.
let keepServerTitle = !!document.head.querySelector('meta[name="seo-rendered"]')
const router = useRouter()
router.afterEach((_to, from) => {
  if (from.name !== undefined && keepServerTitle) {
    keepServerTitle = false
    document.title = site.headerName
    // the rest of the server-rendered metadata describes the landing page only
    document.head
      .querySelectorAll(
        'meta[name="description"], meta[property^="og:"], meta[name^="twitter:"], link[rel="canonical"], link[rel="alternate"][hreflang], script[type="application/ld+json"]',
      )
      .forEach((el) => el.remove())
  }
})
watch(
  () => [site.headerName, site.whiteLabel, site.storefront?.welcome, site.storefront?.logo_url] as const,
  ([n, wl, welcome, logo]) => {
    if (!keepServerTitle || wl) document.title = n
    if (!wl) return
    const setMeta = (sel: string, attr: string, key: string, val: string) => {
      let el = document.head.querySelector<HTMLMetaElement>(sel)
      if (!el) {
        el = document.createElement('meta')
        el.setAttribute(attr, key)
        document.head.appendChild(el)
      }
      el.content = val
    }
    setMeta('meta[name="description"]', 'name', 'description', welcome ?? n)
    setMeta('meta[property="og:site_name"]', 'property', 'og:site_name', n)
    setMeta('meta[property="og:title"]', 'property', 'og:title', n)
    setMeta('meta[property="og:description"]', 'property', 'og:description', welcome ?? n)
    const icon = document.head.querySelector<HTMLLinkElement>('link[rel="icon"]')
    const safe = safeAssetUrl(logo)
    if (icon) {
      if (safe) {
        icon.type = ''
        icon.href = safe
      } else icon.remove() // never show the platform favicon on a white-label store
    }
  },
  { immediate: true },
)

const wide = computed(() => !!route.meta.wide)
const showTabbar = computed(() => !!route.meta.tab)

// App-wide realtime wiring: unread badge + support status. Pages subscribe for their own data.
const offs = [
  rt.on('conversation', (c) => convs.upsert(c)),
  rt.on('message', (e) => {
    const mine = e.message.sender.role === 'buyer'
    if (!mine && !convs.bumpUnread(e.conversation_id, e.message)) void convs.refresh().catch(() => undefined)
  }),
  rt.on('agent_status', (s) => support.set(s)),
]
let syncTick = 0
offs.push(
  rt.on('sync', () => {
    // while polling (every 4 s), refresh the unread badge every ~16 s
    if (syncTick++ % 4 === 0) void convs.refresh().catch(() => undefined)
  }),
)
onBeforeUnmount(() => offs.forEach((off) => off()))

watch(
  () => auth.user?.id,
  (id) => {
    if (id) {
      rt.start()
      void convs.refresh().catch(() => undefined)
    } else {
      rt.stop()
      convs.reset()
    }
  },
  { immediate: true },
)
</script>

<template>
  <SecurityChallenge />
  <div class="shell" :class="{ wide }" :style="{ '--dock-w': wide ? 'var(--shell-wide)' : 'var(--shell-w)' }">
    <div v-if="rt.state === 'polling'" class="rt-banner" role="status">{{ t('common.offlineBanner') }}</div>
    <StorefrontGate><RouterView v-slot="{ Component }">
      <!-- keep the feed + scroll position when coming back from a product page -->
      <KeepAlive :include="['HomePage', 'SearchPage']" :max="2">
        <component :is="Component" />
      </KeepAlive>
    </RouterView></StorefrontGate>
    <TabBar v-if="showTabbar" />
    <CsBubble />
    <AccessBlockedView v-if="accessBlock" :block="accessBlock" />
  </div>
</template>

<style scoped>
.rt-banner {
  position: sticky;
  top: 0;
  z-index: 60;
  background: var(--c-accent-soft);
  color: var(--c-accent-ink);
  font-size: 12px;
  text-align: center;
  padding: 4px 12px;
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import TabBar from './components/TabBar.vue'
import CsBubble from './components/CsBubble.vue'
import { useAuthStore } from './stores/auth'
import { useRealtimeStore } from './stores/realtime'
import { useConversationsStore } from './stores/conversations'
import { useSupportStore } from './stores/support'

const route = useRoute()
const { t } = useI18n()
const auth = useAuthStore()
const rt = useRealtimeStore()
const convs = useConversationsStore()
const support = useSupportStore()

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
  <div class="shell" :class="{ wide }" :style="{ '--dock-w': wide ? 'var(--shell-wide)' : 'var(--shell-w)' }">
    <div v-if="rt.state === 'polling'" class="rt-banner" role="status">{{ t('common.offlineBanner') }}</div>
    <RouterView v-slot="{ Component }">
      <!-- keep the feed + scroll position when coming back from a product page -->
      <KeepAlive :include="['HomePage', 'SearchPage']" :max="2">
        <component :is="Component" />
      </KeepAlive>
    </RouterView>
    <TabBar v-if="showTabbar" />
    <CsBubble />
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

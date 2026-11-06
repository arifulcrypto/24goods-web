<script setup lang="ts">
/** §5 floating customer-service bubble: every buyer page except chat / login / register. */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useConversationsStore } from '../stores/conversations'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const convs = useConversationsStore()

const visible = computed(() => !route.meta.nobubble)
const unread = computed(() => (convs.totalUnread > 99 ? '99+' : convs.totalUnread || ''))
/** Pages with their own fixed bottom bar push the bubble up. */
const lift = computed(() => {
  const n = String(route.name ?? '')
  if (n === 'product' || n === 'buy') return 'bar'
  if (n === 'cart') return 'cart'
  if (n === 'order') return 'bar'
  return route.meta.tab ? 'tab' : 'none'
})

function open() {
  const n = String(route.name ?? '')
  const id = typeof route.params.id === 'string' ? route.params.id : ''
  if ((n === 'product' || n === 'buy') && id) void router.push({ path: '/chat', query: { product: id } })
  else void router.push('/chat')
}
</script>

<template>
  <div v-if="visible" class="cs-wrap dock" :class="`lift-${lift}`">
    <button type="button" class="cs-bubble" :aria-label="t('cs.bubble')" @click="open">
      <van-icon name="service-o" size="24" aria-hidden="true" />
      <span v-if="unread" class="cs-badge">{{ unread }}</span>
    </button>
  </div>
</template>

<style scoped>
.cs-wrap {
  bottom: 0;
  z-index: 55;
  height: 0;
  pointer-events: none;
}
.cs-bubble {
  position: absolute;
  right: 12px;
  bottom: calc(var(--safe-bottom) + 16px);
  width: 48px;
  height: 48px;
  border: 0;
  border-radius: 50%;
  background: var(--c-accent);
  color: var(--c-on-accent);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22);
  display: grid;
  place-items: center;
  cursor: pointer;
  pointer-events: auto;
}
.lift-tab .cs-bubble {
  bottom: calc(var(--tabbar-h) + var(--safe-bottom) + 16px);
}
.lift-bar .cs-bubble {
  bottom: calc(var(--safe-bottom) + 76px);
}
.lift-cart .cs-bubble {
  bottom: calc(var(--tabbar-h) + var(--safe-bottom) + 84px);
}
.cs-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: #e5484d;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { client } from '../api'
import { completeChallenge, siteAccessBlocked, siteAccessCleared, type Challenge } from '../lib/securityChallenge'
import { toastError } from '../ui'

const { t } = useI18n()
const route = useRoute()
/** Catalog routes: the whole page is gated data, so the gate replaces the page. */
const protectedRoute = computed(() => ['home', 'product', 'buy', 'search', 'help', 'article'].includes(String(route.name)))
const blocked = ref(true)
const busy = ref(false)
const failed = ref(false)
/**
 * Elsewhere (account, cart, chat, orders) only some widgets read gated data and login/order/payment
 * recovery must keep working, so a 428 there shows an inline banner instead (issue 48 §1.2).
 */
const hitOutside = ref(false)
const banner = computed(() => blocked.value && hitOutside.value && !protectedRoute.value)

async function check() {
  try {
    const s = await client.get<{ required: boolean; cleared: boolean }>('/security/site-clearance')
    blocked.value = s.required && !s.cleared
    failed.value = false
  } catch (e) {
    failed.value = true
    toastError(e)
  }
}
async function clear() {
  if (busy.value) return
  busy.value = true
  try {
    const ch = await client.post<Challenge>('/security/challenges', { action: 'site_visit' })
    await client.post('/security/site-clearance', await completeChallenge(ch))
    await check()
    if (!blocked.value) {
      hitOutside.value = false
      siteAccessCleared.value++ // views that hit the 428 retry their reads
    }
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
function visible() {
  if (!document.hidden && protectedRoute.value) void check()
}
onMounted(() => {
  void check()
  document.addEventListener('visibilitychange', visible)
})
watch(siteAccessBlocked, (blockedByServer) => {
  if (!blockedByServer) return
  blocked.value = true
  if (!protectedRoute.value) hitOutside.value = true
  siteAccessBlocked.value = false
  void check()
})
const timer = setInterval(visible, 60000)
onBeforeUnmount(() => {
  clearInterval(timer)
  document.removeEventListener('visibilitychange', visible)
})
watch(
  () => route.fullPath,
  () => {
    hitOutside.value = false
    if (protectedRoute.value) void check()
  },
)
</script>

<template>
  <section v-if="blocked && protectedRoute" class="visit-gate">
    <h1>{{ t('challenge.visit') }}</h1>
    <p>{{ t('challenge.visitNote') }}</p>
    <van-button v-if="!failed" type="primary" :loading="busy" @click="clear">{{ t('challenge.verify') }}</van-button>
    <van-button v-else @click="check">{{ t('common.retry') }}</van-button>
  </section>
  <template v-else>
    <div v-if="banner" class="visit-banner" role="status">
      <span>{{ t('challenge.visitBanner') }}</span>
      <van-button size="small" type="primary" round :loading="busy" class="visit-btn" @click="clear">{{ t('challenge.verify') }}</van-button>
    </div>
    <slot />
  </template>
</template>

<style scoped>
.visit-gate {
  max-width: 440px;
  margin: 15vh auto;
  padding: 24px;
  text-align: center;
}
.visit-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-line);
}
.visit-btn {
  flex-shrink: 0;
  min-height: 44px;
}
</style>

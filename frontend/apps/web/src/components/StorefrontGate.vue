<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { client } from '../api'
import { completeChallenge, siteAccessBlocked, siteAccessCleared, type Challenge } from '../lib/securityChallenge'
import { toastError } from '../ui'
import { gateView } from '../lib/siteGate'

const { t } = useI18n()
const route = useRoute()
const blocked = ref(true)
const busy = ref(false)
const failed = ref(false)
/**
 * Every route is gated (v9 §7). A 428 raised mid-session, after the visitor was cleared, shows a
 * docked banner instead so in-progress work survives; the next navigation gates the whole page.
 */
const midSession = ref(false)
const view = computed(() => gateView({ blocked: blocked.value, midSession: midSession.value }))

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
      midSession.value = false
      siteAccessCleared.value++ // views that hit the 428 retry their reads
    }
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
function visible() {
  if (!document.hidden) void check()
}
onMounted(() => {
  void check()
  document.addEventListener('visibilitychange', visible)
})
watch(siteAccessBlocked, (blockedByServer) => {
  if (!blockedByServer) return
  blocked.value = true
  midSession.value = true
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
    midSession.value = false
    void check()
  },
)
</script>

<template>
  <section v-if="view === 'gate'" class="visit-gate">
    <h1>{{ t('challenge.visit') }}</h1>
    <p>{{ t('challenge.visitNote') }}</p>
    <van-button v-if="!failed" type="primary" :loading="busy" @click="clear">{{ t('challenge.verify') }}</van-button>
    <van-button v-else @click="check">{{ t('common.retry') }}</van-button>
  </section>
  <template v-else>
    <!-- docked above the tab bar: a fixed top nav bar would cover an in-flow banner -->
    <div v-if="view === 'banner'" class="dock visit-banner" role="alert">
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
  bottom: calc(var(--tabbar-h) + var(--safe-bottom) + 8px);
  margin: 0 auto;
  width: calc(100% - 24px);
  max-width: calc(var(--dock-w, var(--shell-w)) - 24px);
  z-index: 60;
  border-radius: var(--radius-m);
  box-shadow: var(--shadow-1);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
  background: var(--c-surface);
  border: 1px solid var(--c-line);
}
.visit-btn {
  flex-shrink: 0;
  min-height: 44px;
}
</style>

<script setup lang="ts">
/** One-time prompt for the newest unread announcement (per-store setting `prompt_new`). */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'
import { useAnnouncementsStore } from '../stores/announcements'
import { useSiteStore } from '../stores/site'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const site = useSiteStore()
const store = useAnnouncementsStore()
const shownId = ref<string | null>(null)
const show = computed({
  get: () => shownId.value !== null,
  set: (v: boolean) => {
    if (!v) later()
  },
})
const item = computed(() => store.items.find((a) => a.id === shownId.value) ?? null)
// never over the chat, auth pages or the news pages themselves
const quiet = computed(() => !!route.meta.nobubble || route.name === 'chat' || String(route.name ?? '').startsWith('announcement'))

watch(
  () => [store.loaded, store.newestUnread?.id, store.promptNew, quiet.value] as const,
  ([loaded, id, enabled, isQuiet]) => {
    if (!loaded || !enabled || isQuiet || !id || shownId.value || store.prompted(id)) return
    shownId.value = id
  },
  { immediate: true },
)
// a new session / account: look again
watch(
  () => auth.user?.id,
  () => {
    store.reset()
    void site.load().then(() => store.refresh())
  },
  { immediate: true },
)

function later() {
  if (shownId.value) store.markPrompted(shownId.value)
  shownId.value = null
}
function view() {
  const id = shownId.value
  if (!id) return
  store.markPrompted(id)
  shownId.value = null
  void router.push({ name: 'announcement', params: { id } })
}
</script>

<template>
  <van-popup v-model:show="show" round teleport="body" class="annp" :close-on-popstate="true">
    <div v-if="item" class="annp-box" role="dialog" aria-modal="true" :aria-label="t('news.newTitle')">
      <p class="annp-kicker">{{ t('news.newTitle') }}</p>
      <h2>{{ item.title }}</h2>
      <p class="annp-cat">{{ t(`news.cat.${item.category}`) }}</p>
      <div class="annp-actions">
        <van-button block round plain size="large" @click="later">{{ t('news.later') }}</van-button>
        <van-button block round type="primary" size="large" @click="view">{{ t('news.view') }}</van-button>
      </div>
    </div>
  </van-popup>
</template>

<style scoped>
.annp {
  width: min(86vw, 380px);
}
.annp-box {
  padding: 20px 16px 16px;
}
.annp-kicker {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--c-text-2);
}
.annp-box h2 {
  margin: 0 0 6px;
  font-size: 18px;
  overflow-wrap: anywhere;
}
.annp-cat {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--c-text-2);
}
.annp-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
</style>

<script setup lang="ts">
/** News: list at /announcements, detail at /announcements/:id (marks it read). */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SafeHtml } from '@24goods/shared/ui'
import { formatDateTime, goBack, type AnnouncementDetail } from '@24goods/shared'
import { api } from '../api'
import { toastError } from '../ui'
import { useAnnouncementsStore } from '../stores/announcements'
import { useAuthStore } from '../stores/auth'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const store = useAnnouncementsStore()
const auth = useAuthStore()
const detail = ref<AnnouncementDetail | null>(null)
const loading = ref(false)
const id = computed(() => (typeof route.params.id === 'string' ? route.params.id : ''))

async function loadDetail() {
  detail.value = null
  if (!id.value) return
  loading.value = true
  try {
    detail.value = await api.announcements.get(id.value)
    void store.markRead(id.value)
  } catch (e) {
    toastError(e)
    void router.replace({ name: 'announcements' })
  } finally {
    loading.value = false
  }
}
onMounted(async () => {
  await auth.init()
  await store.refresh()
  void loadDetail()
})
watch(id, () => void loadDetail())
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('news.title')" left-arrow :border="false" @click-left="id ? router.push({ name: 'announcements' }) : goBack(router, '/')">
      <template v-if="!id && store.unread > 0" #right>
        <button type="button" class="all" @click="store.markAll()">{{ t('news.markAll') }}</button>
      </template>
    </van-nav-bar>

    <template v-if="id">
      <div v-if="loading && !detail" class="pad"><van-skeleton :row="4" /></div>
      <article v-else-if="detail" class="detail">
        <p class="meta">
          <van-tag plain>{{ t(`news.cat.${detail.category}`) }}</van-tag>
          <span>{{ formatDateTime(detail.published_at, locale) }}</span>
        </p>
        <h1>{{ detail.title }}</h1>
        <SafeHtml class="rich" :html="detail.body_html" />
      </article>
    </template>

    <template v-else>
      <ul v-if="store.items.length" class="list" role="list">
        <li v-for="a in store.items" :key="a.id">
          <RouterLink class="row" :to="{ name: 'announcement', params: { id: a.id } }">
            <span class="dot" :class="{ on: !store.isRead(a) }" aria-hidden="true" />
            <span class="main">
              <span class="title" :class="{ unread: !store.isRead(a) }">{{ a.title }}</span>
              <span class="sub">
                <van-tag v-if="a.pinned" type="primary" plain>{{ t('news.pinned') }}</van-tag>
                {{ t(`news.cat.${a.category}`) }} · {{ formatDateTime(a.published_at, locale) }}
              </span>
            </span>
            <van-icon name="arrow" aria-hidden="true" />
          </RouterLink>
        </li>
      </ul>
      <van-empty v-else-if="store.loaded" image="search" :description="t('news.empty')" />
    </template>
  </div>
</template>

<style scoped>
.all {
  min-height: 44px;
  padding: 0 4px;
  border: 0;
  background: none;
  color: var(--van-nav-bar-icon-color, #1989fa);
  font: inherit;
  font-size: 13px;
}
.pad {
  padding: 16px;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  background: var(--c-surface);
}
.row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 56px;
  padding: 8px 16px;
  color: inherit;
  text-decoration: none;
  border-bottom: 1px solid var(--c-border, rgba(0, 0, 0, 0.06));
}
.dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: transparent;
}
.dot.on {
  background: var(--c-danger, #ee0a24);
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.title {
  overflow-wrap: anywhere;
}
.title.unread {
  font-weight: 700;
}
.sub {
  font-size: 12px;
  color: var(--c-text-2);
}
.detail {
  padding: 16px;
}
.detail h1 {
  margin: 8px 0 12px;
  font-size: 20px;
  overflow-wrap: anywhere;
}
.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 12px;
  color: var(--c-text-2);
}
.rich {
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.rich :deep(p),
.rich :deep(ul),
.rich :deep(ol) {
  margin: 0 0 10px;
}
</style>

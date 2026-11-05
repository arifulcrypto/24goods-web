<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { formatRelative, messagePreview, type Conversation } from '@24goods/shared'
import { api } from '../api'
import { useConversationsStore } from '../stores/conversations'
import { toastError } from '../ui'

const { t, locale } = useI18n()
const router = useRouter()
const convs = useConversationsStore()
const loading = ref(!convs.loaded)
const refreshing = ref(false)
const starting = ref(false)

async function load() {
  try {
    await convs.refresh()
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}
onMounted(load)

function title(c: Conversation): string {
  return c.assigned_agent?.display_name || t('messages.support')
}
function open(c: Conversation) {
  void router.push({ path: '/chat', query: { cid: c.id } })
}
async function contact() {
  if (starting.value) return
  starting.value = true
  try {
    const c = await api.conversations.open()
    convs.upsert(c)
    open(c)
  } catch (e) {
    toastError(e)
  } finally {
    starting.value = false
  }
}
</script>

<template>
  <div class="page with-tabbar">
    <van-nav-bar :title="t('messages.title')" class="dock-top" fixed placeholder safe-area-inset-top />
    <van-pull-refresh v-model="refreshing" @refresh="load">
      <div v-if="loading" class="list">
        <div v-for="i in 3" :key="i" class="row">
          <div class="skeleton avatar" />
          <div class="row-main">
            <div class="skeleton" style="height: 16px; width: 40%" />
            <div class="skeleton" style="height: 14px; width: 80%; margin-top: 8px" />
          </div>
        </div>
      </div>
      <template v-else-if="convs.list.length">
        <ul class="list" role="list">
          <li v-for="c in convs.list" :key="c.id">
            <button type="button" class="row" @click="open(c)">
              <van-badge :content="c.unread > 99 ? '99+' : c.unread || ''" :offset="[-4, 4]">
                <span class="avatar" aria-hidden="true">{{ title(c).charAt(0).toUpperCase() }}</span>
              </van-badge>
              <span class="row-main">
                <span class="row-top">
                  <span class="row-title">{{ title(c) }}</span>
                  <span class="row-time muted">{{ formatRelative(c.updated_at, locale) }}</span>
                </span>
                <span v-if="c.product" class="row-product">{{ c.product.title }}</span>
                <span class="row-preview muted">
                  <van-tag v-if="c.status === 'closed'" plain>{{ t('messages.closed') }}</van-tag>
                  {{ messagePreview(c.last_message, t) }}
                </span>
              </span>
              <img v-if="c.product?.cover" :src="c.product.cover" alt="" class="row-cover" loading="lazy" />
            </button>
          </li>
        </ul>
      </template>
      <van-empty v-else image="default" :description="t('messages.empty')">
        <van-button round type="primary" :loading="starting" @click="contact">{{ t('messages.contact') }}</van-button>
      </van-empty>
    </van-pull-refresh>
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
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
  gap: 12px;
  width: 100%;
  min-height: 72px;
  padding: 10px 16px;
  border: 0;
  border-bottom: 1px solid var(--c-line);
  background: var(--c-surface);
  font: inherit;
  color: inherit;
  text-align: start;
  cursor: pointer;
}
.avatar {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: #2b2b2b;
  color: var(--c-accent);
  display: grid;
  place-items: center;
  font-size: 18px;
  font-weight: 700;
  flex: none;
}
.row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.row-top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.row-title {
  font-weight: 600;
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.row-time {
  font-size: 12px;
  white-space: nowrap;
}
.row-product {
  font-size: 12px;
  color: var(--c-accent-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.row-preview {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.row-cover {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  object-fit: cover;
  flex: none;
}
</style>

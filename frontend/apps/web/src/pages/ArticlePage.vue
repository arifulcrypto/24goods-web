<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { formatDateTime, formatNumber, goBack, isApiError, type Article } from '@24goods/shared'
import { SafeHtml } from '@24goods/shared/ui'
import { api } from '../api'
import { useNoindex } from '../composables/useNoindex'
import { toastError } from '../ui'
import SiteFooter from '../components/SiteFooter.vue'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const article = ref<Article | null>(null)
const loading = ref(true)
const notFound = ref(false)
useNoindex(() => notFound.value)

watch(
  () => route.params.slug,
  async (slug) => {
    if (typeof slug !== 'string' || !slug) return
    loading.value = true
    notFound.value = false
    try {
      article.value = await api.articles.get(slug)
    } catch (e) {
      article.value = null
      if (isApiError(e) && e.code === 'not_found') notFound.value = true
      else toastError(e)
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="page">
    <van-nav-bar :title="article?.title || t('help.title')" class="dock-top" left-arrow fixed placeholder safe-area-inset-top @click-left="goBack(router, '/help')" />
    <div v-if="loading" class="section card pad"><div class="skeleton" style="height: 120px" /></div>
    <van-empty v-else-if="notFound || !article" image="error" :description="t('errors.not_found')" />
    <article v-else class="section card pad">
      <h1>{{ article.title }}</h1>
      <div class="meta muted">
        <span v-if="article.published_at">{{ t('help.published', { date: formatDateTime(article.published_at, locale) }) }}</span>
        <span v-if="article.updated_at && article.updated_at !== article.published_at">{{ t('help.updated', { date: formatDateTime(article.updated_at, locale) }) }}</span>
        <span>{{ t('help.views', { n: formatNumber(article.views, locale) }) }}</span>
      </div>
      <SafeHtml class="body" :html="article.body_html" />
    </article>
    <SiteFooter />
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding-bottom: calc(var(--safe-bottom) + 16px);
}
.pad {
  padding: 16px;
}
h1 {
  font-size: 20px;
  margin: 0 0 8px;
  overflow-wrap: anywhere;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  font-size: 12px;
  margin-bottom: 14px;
}
.body {
  line-height: 1.75;
  overflow-wrap: anywhere;
  color: var(--c-text-2);
  font-size: 14px;
}
.body :deep(h2),
.body :deep(h3) {
  color: var(--c-text);
  font-size: 16px;
  margin: 16px 0 6px;
}
.body :deep(a) {
  color: var(--c-price);
  text-decoration: underline;
}
.body :deep(img) {
  max-width: 100%;
  height: auto;
}
.body :deep(table) {
  display: block;
  overflow-x: auto;
  max-width: 100%;
  border-collapse: collapse;
}
.body :deep(td),
.body :deep(th) {
  border: 1px solid var(--c-line);
  padding: 4px 8px;
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { formatDateTime, formatNumber, goBack, type ArticleSummary } from '@24goods/shared'
import { api } from '../api'
import { toastError } from '../ui'
import SiteFooter from '../components/SiteFooter.vue'

const { t, te, locale } = useI18n()
const router = useRouter()
const items = ref<ArticleSummary[]>([])
const loading = ref(true)

let seq = 0
async function load() {
  const mine = ++seq
  try {
    const r = await api.articles.list({ page: 1, page_size: 100 })
    if (mine === seq) items.value = r.items
  } catch (e) {
    if (mine === seq) toastError(e)
  } finally {
    if (mine === seq) loading.value = false
  }
}
onMounted(load)
// Titles and bodies come back in the request locale.
watch(locale, load)

/** Category slugs are stored raw ("faq", "tutorial"); show a translated label when we know it. */
function catLabel(name: string): string {
  const key = `help.categories.${name}`
  return te(key) ? t(key) : name
}

const policies = [
  { to: '/a/rules', key: 'me.rules', icon: 'description-o' },
  { to: '/a/after-sales', key: 'me.afterSalesPolicy', icon: 'shield-o' },
  { to: '/a/terms', key: 'me.terms', icon: 'notes-o' },
  { to: '/a/privacy', key: 'me.privacy', icon: 'eye-o' },
]

const groups = computed(() => {
  const sorted = [...items.value].sort((a, b) => Number(b.pinned) - Number(a.pinned) || Date.parse(b.published_at ?? '') - Date.parse(a.published_at ?? ''))
  const m = new Map<string, ArticleSummary[]>()
  for (const a of sorted) m.set(a.category || '', [...(m.get(a.category || '') ?? []), a])
  return [...m.entries()].map(([name, list]) => ({ name, list }))
})
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('help.title')" class="dock-top" left-arrow fixed placeholder safe-area-inset-top @click-left="goBack(router, '/')" />
    <section class="section card">
      <van-cell :title="t('help.guide')" :label="t('help.guideHint')" icon="guide-o" is-link to="/a/help" />
    </section>
    <div v-if="loading" class="section card pad"><div class="skeleton" style="height: 18px" /></div>
    <van-empty v-else-if="!items.length" image="default" :description="t('help.empty')" />
    <section v-for="g in groups" :key="g.name" class="section">
      <h2 v-if="g.name" class="cat">{{ catLabel(g.name) }}</h2>
      <div class="card list">
        <RouterLink v-for="a in g.list" :key="a.slug" :to="`/a/${encodeURIComponent(a.slug)}`" class="item">
          <span class="ititle">
            <span v-if="a.pinned" class="pin">{{ t('help.pinned') }}</span>{{ a.title }}
          </span>
          <span class="muted imeta">{{ formatDateTime(a.published_at, locale) }} · {{ t('help.views', { n: formatNumber(a.views, locale) }) }}</span>
        </RouterLink>
      </div>
    </section>
    <section class="section">
      <h2 class="cat">{{ t('help.policies') }}</h2>
      <div class="card">
        <van-cell v-for="p in policies" :key="p.to" :title="t(p.key)" :icon="p.icon" is-link :to="p.to" />
      </div>
    </section>
    <SiteFooter />
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding-bottom: calc(var(--safe-bottom) + 16px);
}
.pad {
  padding: 14px 16px;
}
.cat {
  font-size: 14px;
  margin: 0 4px 6px;
  color: var(--c-text-2);
}
.list {
  overflow: hidden;
}
.item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 56px;
  justify-content: center;
  padding: 8px 16px;
  text-decoration: none;
  color: inherit;
  border-bottom: 1px solid var(--c-line);
}
.item:last-child {
  border-bottom: 0;
}
.ititle {
  font-size: 15px;
  overflow-wrap: anywhere;
}
.pin {
  display: inline-block;
  font-size: 11px;
  line-height: 18px;
  padding: 0 6px;
  margin-right: 6px;
  border-radius: 4px;
  background: var(--c-accent-soft);
  color: var(--c-accent-ink);
}
.imeta {
  font-size: 12px;
}
</style>

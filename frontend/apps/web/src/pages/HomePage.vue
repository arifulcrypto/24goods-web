<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { Category } from '@24goods/shared'
import { api } from '../api'
import ProductGrid from '../components/ProductGrid.vue'
import SupportPill from '../components/SupportPill.vue'
import { useProductFeed } from '../composables/useProductFeed'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const categories = ref<Category[]>([])
const category = ref('')
const refreshing = ref(false)
const feed = useProductFeed(() => ({ category: category.value || undefined }))

onMounted(async () => {
  try {
    categories.value = await api.categories()
  } catch {
    /* chips are optional */
  }
})

function pick(key: string) {
  if (category.value === key) return
  category.value = key
  feed.reset()
  void feed.load()
}
async function onRefresh() {
  feed.reset()
  await feed.load()
  refreshing.value = false
}
function openSupport() {
  void router.push(auth.loggedIn ? '/chat' : { name: 'login', query: { redirect: '/chat' } })
}
</script>

<template>
  <div class="home with-tabbar">
    <header class="hero">
      <div class="hero-top">
        <div class="brand">
          <span class="logo" aria-hidden="true">24</span>
          <span class="brand-name">{{ t('app.name') }}</span>
        </div>
        <SupportPill @click="openSupport" />
      </div>
      <RouterLink to="/search" class="searchbar" role="search">
        <van-icon name="search" size="18" aria-hidden="true" />
        <span>{{ t('home.searchPlaceholder') }}</span>
      </RouterLink>
      <div class="chips" role="tablist" :aria-label="t('product.category')">
        <button
          type="button"
          role="tab"
          class="chip"
          :class="{ on: category === '' }"
          :aria-selected="category === ''"
          @click="pick('')"
        >
          {{ t('home.allCategories') }}
        </button>
        <button
          v-for="c in categories"
          :key="c.key"
          type="button"
          role="tab"
          class="chip"
          :class="{ on: category === c.key }"
          :aria-selected="category === c.key"
          @click="pick(c.key)"
        >
          {{ c.name }}
        </button>
      </div>
    </header>

    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <h2 class="section-title">{{ t('home.recommended') }}</h2>
      <van-list
        v-model:loading="feed.loading.value"
        v-model:error="feed.error.value"
        :finished="feed.finished.value"
        :finished-text="feed.items.value.length ? t('common.noMore') : ''"
        :error-text="t('home.loadFailed')"
        :loading-text="t('common.loading')"
        :offset="400"
        @load="feed.load"
      >
        <ProductGrid :items="feed.items.value" :skeleton="feed.loading.value && !feed.items.value.length ? 6 : 0" />
        <van-empty
          v-if="feed.finished.value && !feed.items.value.length"
          image="search"
          :description="t('home.emptyTitle')"
        />
      </van-list>
    </van-pull-refresh>
  </div>
</template>

<style scoped>
.hero {
  background: linear-gradient(180deg, var(--c-accent) 0%, #ffe680 70%, var(--c-bg) 100%);
  padding: calc(var(--safe-top) + 10px) 12px 6px;
}
.hero-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 44px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.logo {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: #1f1f1f;
  color: var(--c-accent);
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 15px;
}
.brand-name {
  font-weight: 800;
  font-size: 19px;
  letter-spacing: -0.02em;
}
.searchbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  min-height: 44px;
  padding: 0 14px;
  border-radius: 999px;
  background: var(--c-surface);
  color: var(--c-muted);
  text-decoration: none;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(122, 79, 0, 0.08);
}
.searchbar span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.chips {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding: 10px 0 4px;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}
.chips::-webkit-scrollbar {
  display: none;
}
.chip {
  flex: none;
  min-height: 36px;
  padding: 0 14px;
  border-radius: 999px;
  border: 0;
  background: rgba(255, 255, 255, 0.7);
  color: var(--c-text-2);
  font-size: 13px;
  cursor: pointer;
  position: relative;
}
.chip::after {
  content: '';
  position: absolute;
  inset: -4px 0;
}
.chip.on {
  background: #1f1f1f;
  color: var(--c-accent);
  font-weight: 600;
}
.section-title {
  font-size: 16px;
  margin: 8px 12px 10px;
}
@media (min-width: 768px) {
  .hero {
    padding-left: 16px;
    padding-right: 16px;
  }
  .section-title {
    margin-left: 16px;
  }
}
</style>

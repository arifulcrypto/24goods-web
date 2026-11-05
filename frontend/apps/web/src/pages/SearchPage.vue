<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { goBack, type ProductSummary } from '@24goods/shared'
import BuySheet from '../components/BuySheet.vue'
import ProductList from '../components/ProductList.vue'
import { useProductFeed } from '../composables/useProductFeed'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const q = ref('')
const input = ref('')
const feed = useProductFeed(() => ({ q: q.value }))
const buying = ref<ProductSummary | null>(null)
const buySheet = ref(false)

// Only react while this (kept-alive) page is the active route.
watch(
  () => (route.name === 'search' ? (typeof route.query.q === 'string' ? route.query.q.trim().slice(0, 100) : '') : null),
  (v) => {
    if (v === null || (v === q.value && feed.items.value.length)) return
    q.value = v
    input.value = v
    feed.reset()
    if (v) void feed.load()
    else feed.finished.value = true
  },
  { immediate: true },
)

function submit() {
  const v = input.value.trim()
  void router.replace({ path: '/search', query: v ? { q: v } : {} })
}
function back() {
  goBack(router, '/')
}
function buy(p: ProductSummary) {
  buying.value = p
  buySheet.value = true
}
</script>

<template>
  <div class="search-page">
    <div class="search-head">
      <button type="button" class="back tap" :aria-label="t('common.back')" @click="back">
        <van-icon name="arrow-left" size="20" />
      </button>
      <form class="search-form" action="/search" role="search" @submit.prevent="submit">
        <van-search
          v-model="input"
          shape="round"
          :placeholder="t('home.searchPlaceholder')"
          :autofocus="!q"
          maxlength="100"
          @search="submit"
        />
      </form>
      <button type="button" class="go tap" @click="submit">{{ t('common.search') }}</button>
    </div>

    <p v-if="q && feed.total.value !== null && feed.items.value.length" class="result-note muted">
      {{ t('search.resultsFor', { q }) }}
    </p>

    <van-list
      v-if="q"
      v-model:loading="feed.loading.value"
      v-model:error="feed.error.value"
      :finished="feed.finished.value"
      :finished-text="feed.items.value.length ? t('common.noMore') : ''"
      :error-text="t('home.loadFailed')"
      :immediate-check="false"
      :offset="400"
      @load="feed.load"
    >
      <ProductList :items="feed.items.value" view="list" :skeleton="feed.loading.value && !feed.items.value.length ? 4 : 0" @buy="buy" />
      <van-empty v-if="feed.finished.value && !feed.items.value.length" image="search" :description="t('search.noResults', { q })">
        <p class="muted">{{ t('search.hint') }}</p>
      </van-empty>
    </van-list>
    <van-empty v-else image="search" :description="t('search.typeToSearch')" />
    <BuySheet v-model:show="buySheet" :product="buying" />
  </div>
</template>

<style scoped>
.search-page {
  min-height: 100vh;
  padding-bottom: calc(var(--safe-bottom) + 16px);
}
.search-head {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: calc(var(--safe-top) + 4px) 4px 4px;
  background: var(--c-surface);
  box-shadow: 0 1px 0 var(--c-line);
}
.back,
.go {
  border: 0;
  background: none;
  display: grid;
  place-items: center;
  cursor: pointer;
  color: var(--c-text);
  font-size: 15px;
  padding: 0 8px;
}
.search-form {
  flex: 1;
  min-width: 0;
}
.search-form :deep(.van-search) {
  padding: 6px 4px;
}
.result-note {
  margin: 10px 12px;
  font-size: 13px;
}
@media (min-width: 768px) {
  .result-note {
    margin-left: 16px;
  }
}
</style>

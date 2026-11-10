<script setup lang="ts">
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { LogoMark } from '@24goods/shared/ui'
import { BRAND, CATALOG_SORTS, recentIds, safeAssetUrl, storeInitial, type CatalogSort, type Category, type ProductSummary } from '@24goods/shared'
import { api } from '../api'
import Announcement from '../components/Announcement.vue'
import AnnouncementBell from '../components/AnnouncementBell.vue'
import BuySheet from '../components/BuySheet.vue'
import MiniStrip from '../components/MiniStrip.vue'
import ProductList from '../components/ProductList.vue'
import SupportPill from '../components/SupportPill.vue'
import { openSupport as support } from '../lib/support'
import { useProductFeed } from '../composables/useProductFeed'
import { useSiteStore } from '../stores/site'

const VIEW_KEY = 'kesiv.view'
const SORT_KEY = 'kesiv.sort'
const { t, locale } = useI18n()
const router = useRouter()
const categories = ref<Category[]>([])
const category = ref('')
const refreshing = ref(false)
const view = ref<'list' | 'grid'>(readView())
// the shopper's own sort (remembered) wins; otherwise the store default decides server-side
const chosenSort = ref<CatalogSort | ''>(readSort())
const feed = useProductFeed(() => ({ category: category.value || undefined, sort: chosenSort.value || undefined }), 50)
const recommended = ref<ProductSummary[]>([])
const buying = ref<ProductSummary | null>(null)
const buySheet = ref(false)

const site = useSiteStore()
const loc = computed(() => (locale.value === 'zh-CN' ? 'zh-CN' : 'en'))
// v5: storefront name > Settings → General override > brand.ts
const brandTitle = computed(() => {
  if (site.storefront) return site.storefront.store_name
  if (site.site?.general?.site_name_i18n?.[loc.value]?.trim()) return site.siteName
  return loc.value === 'zh-CN' ? `${BRAND.nameZh} ${BRAND.name}` : BRAND.name
})
const tagline = computed(() => site.whiteLabel ? '' : site.site?.general?.subtitle_i18n?.[loc.value]?.trim() || BRAND.tagline[loc.value])
const storeLogo = computed(() => safeAssetUrl(site.storefront?.logo_url))
const welcome = computed(() => site.storefront?.welcome ?? null)

function readSort(): CatalogSort | '' {
  try {
    const v = localStorage.getItem(SORT_KEY)
    return (CATALOG_SORTS as readonly string[]).includes(v ?? '') ? (v as CatalogSort) : ''
  } catch {
    return ''
  }
}
const shownSort = computed<CatalogSort>(() => chosenSort.value || site.site?.catalog?.default_sort || 'manual')
function setSort(ev: Event) {
  chosenSort.value = (ev.target as HTMLSelectElement).value as CatalogSort
  try {
    localStorage.setItem(SORT_KEY, chosenSort.value)
  } catch {
    /* not remembered */
  }
  feed.reset()
  void feed.load()
}

function readView(): 'list' | 'grid' {
  try {
    return localStorage.getItem(VIEW_KEY) === 'grid' ? 'grid' : 'list'
  } catch {
    return 'list'
  }
}
function setView(v: 'list' | 'grid') {
  view.value = v
  try {
    localStorage.setItem(VIEW_KEY, v)
  } catch {
    /* ignore */
  }
}

// a newer load (or a language switch) supersedes older responses, so old-locale text never lands
let stripSeq = 0
async function loadStrips() {
  const mine = ++stripSeq
  // v11: "Recently viewed" lives on My account now (a shorter home); the viewed ids still steer
  // the recommendations below
  const ids = recentIds()
  let rec: ProductSummary[] = []
  try {
    rec = (await api.recommended({ recent: ids, limit: 8 })).items
  } catch {
    rec = []
  }
  if (mine === stripSeq) recommended.value = rec
}
let catSeq = 0
async function loadCategories() {
  const mine = ++catSeq
  try {
    const c = await api.categories()
    if (mine === catSeq) categories.value = c
  } catch {
    /* chips are optional */
  }
}
onMounted(() => {
  void loadStrips()
  void loadCategories()
})
// server-localized strips and chips follow the language (the feed refetches itself); keep the category
watch(locale, () => {
  void loadStrips()
  void loadCategories()
})
// kept alive: refresh the recommendations when coming back from a product page
let first = true
onActivated(() => {
  if (first) first = false
  else void loadStrips()
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
  void loadStrips()
  refreshing.value = false
}
function buy(p: ProductSummary) {
  buying.value = p
  buySheet.value = true
}
function openSupport() {
  void support(router)
}
</script>

<template>
  <div class="home with-tabbar">
    <header class="hero">
      <div class="hero-top">
        <div class="brand">
          <img v-if="storeLogo" :src="storeLogo" class="store-logo" width="32" height="32" alt="" />
          <span v-else-if="site.whiteLabel" class="store-mono" aria-hidden="true">{{ storeInitial(site.storefront?.store_name) }}</span>
          <LogoMark v-else :size="32" />
          <div class="brand-text">
            <span class="brand-name">{{ brandTitle }}</span>
            <span v-if="tagline" class="brand-tag">{{ tagline }}</span>
          </div>
        </div>
        <div class="hero-actions">
          <AnnouncementBell />
          <SupportPill @click="openSupport" />
        </div>
      </div>
      <p v-if="welcome" class="store-welcome">{{ welcome }}</p>
      <nav class="links" :aria-label="t('home.quickLinks')">
        <RouterLink v-if="site.features.lookup" to="/lookup"><van-icon name="search" aria-hidden="true" />{{ t('home.lookup') }}</RouterLink>
        <RouterLink to="/help"><van-icon name="question-o" aria-hidden="true" />{{ t('home.help') }}</RouterLink>
        <button type="button" @click="openSupport"><van-icon name="service-o" aria-hidden="true" />{{ t('home.support') }}</button>
      </nav>
      <RouterLink to="/search" class="searchbar" role="search">
        <van-icon name="search" size="18" aria-hidden="true" />
        <span>{{ t('home.searchPlaceholder') }}</span>
      </RouterLink>
      <div class="chips" role="tablist" :aria-label="t('product.category')">
        <button type="button" role="tab" class="chip" :class="{ on: category === '' }" :aria-selected="category === ''" @click="pick('')">
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

    <Announcement />

    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <MiniStrip :title="t('home.recommended')" :items="recommended" />

      <div class="list-head">
        <h2>{{ t('home.allProducts') }}</h2>
        <label class="sort">
          <span class="sr-only">{{ t('home.sortBy') }}</span>
          <select :value="shownSort" :aria-label="t('home.sortBy')" @change="setSort">
            <option v-for="o in CATALOG_SORTS" :key="o" :value="o">{{ t(`home.sort.${o}`) }}</option>
          </select>
        </label>
        <div class="seg" role="radiogroup" :aria-label="t('home.viewMode')">
          <button type="button" role="radio" :aria-checked="view === 'list'" :aria-label="t('home.viewList')" :class="{ on: view === 'list' }" @click="setView('list')">
            <van-icon name="bars" />
          </button>
          <button type="button" role="radio" :aria-checked="view === 'grid'" :aria-label="t('home.viewGrid')" :class="{ on: view === 'grid' }" @click="setView('grid')">
            <van-icon name="apps-o" />
          </button>
        </div>
      </div>
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
        <ProductList
          :items="feed.items.value"
          :view="view"
          :categories="categories"
          :grouped="category === ''"
          :skeleton="feed.loading.value && !feed.items.value.length ? 6 : 0"
          @buy="buy"
        />
        <van-empty v-if="feed.finished.value && !feed.items.value.length" image="search" :description="t('home.emptyTitle')" />
      </van-list>
    </van-pull-refresh>

    <BuySheet v-model:show="buySheet" :product="buying" />
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
.hero-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: none;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.brand-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.brand-name {
  font-weight: 800;
  font-size: 18px;
  line-height: 20px;
  letter-spacing: -0.02em;
}
.brand-tag {
  font-size: 10px;
  line-height: 13px;
  color: var(--c-text-2);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  max-width: 190px;
}
.links {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}
.links a,
.links button {
  flex: 1;
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border: 0;
  background: none;
  color: var(--c-text);
  font: inherit;
  font-size: 13px;
  text-decoration: none;
  cursor: pointer;
}
.searchbar {
  display: flex;
  align-items: center;
  gap: 8px;
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
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
}
.list-head h2 {
  font-size: 16px;
  margin: 0;
}
.sort {
  margin-inline-start: auto;
  margin-inline-end: 8px;
}
.sort select {
  min-height: 44px;
  max-width: 150px;
  border: 0;
  border-radius: 999px;
  padding: 0 12px;
  background: var(--c-line);
  color: var(--c-text);
  font: inherit;
  font-size: 13px;
}
.seg {
  display: inline-flex;
  background: #ececec;
  border-radius: 999px;
  padding: 2px;
}
.seg button {
  min-height: 44px;
  min-width: 44px;
  border: 0;
  border-radius: 999px;
  background: none;
  color: var(--c-text-2);
  cursor: pointer;
  font-size: 18px;
}
.seg button.on {
  background: var(--c-surface);
  color: var(--c-text);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}
@media (min-width: 768px) {
  .hero {
    padding-left: 16px;
    padding-right: 16px;
  }
}
.store-mono {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #1f1f1f;
  color: var(--c-accent);
  display: grid;
  place-items: center;
  font-weight: 800;
}
.store-logo {
  border-radius: 8px;
  object-fit: cover;
}
.store-welcome {
  margin: 8px 0 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}
</style>

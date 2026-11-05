<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ProductThumb, SafeHtml } from '@24goods/shared/ui'
import {
  addRecent,
  afterSalesBadge,
  formatLabels,
  formatNumber,
  goBack,
  isApiError,
  moneyParts,
  safeLinkUrl,
  type Product,
  type ProductSummary,
} from '@24goods/shared'
import { api } from '../api'
import BuySheet from '../components/BuySheet.vue'
import MiniStrip from '../components/MiniStrip.vue'
import { cleanSrc, rememberSrc } from '../lib/attribution'
import { useAuthStore } from '../stores/auth'
import { showImagePreview, toastError } from '../ui'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const product = ref<Product | null>(null)
const loading = ref(true)
const notFound = ref(false)
const sheet = ref(false)
const chatting = ref(false)
const slide = ref(0)
const related = ref<ProductSummary[]>([])

const id = computed(() => String(route.params.id ?? ''))
const src = computed(() => cleanSrc(route.query.src))
const buyQty = computed(() => {
  const n = Number.parseInt(String(route.query.qty ?? '1'), 10)
  return Number.isFinite(n) ? Math.min(10, Math.max(1, n)) : 1
})
watch(
  id,
  async (v) => {
    if (!v) return
    loading.value = true
    notFound.value = false
    related.value = []
    rememberSrc(src.value)
    try {
      product.value = await api.product(v)
      addRecent(product.value.id)
      if (route.name === 'buy' && !soldOut.value) sheet.value = true
      void api
        .recommended({ for: v, limit: 8 })
        .then((r) => (related.value = r.items.filter((x) => x.id !== v)))
        .catch(() => undefined)
    } catch (e) {
      if (isApiError(e) && e.code === 'not_found') notFound.value = true
      else toastError(e)
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

const images = computed(() => {
  const p = product.value
  if (!p) return []
  const list = p.images.length ? p.images : p.cover ? [p.cover] : []
  return list.slice(0, 12)
})
const price = computed(() => (product.value ? moneyParts(product.value.price, product.value.currency, locale.value) : null))
const isAuto = computed(() => product.value?.delivery_mode === 'auto_card')
const soldOut = computed(() => isAuto.value && (product.value?.stock ?? 0) <= 0)
const as = computed(() => afterSalesBadge(product.value?.after_sales_hours))
const labels = computed(() => formatLabels(product.value?.delivery_format, product.value?.delivery_separator))
const redeem = computed(() => safeLinkUrl(product.value?.redeem_url))

function requireLogin(): boolean {
  if (auth.loggedIn) return true
  void router.push({ name: 'login', query: { redirect: route.fullPath } })
  return false
}

async function chat() {
  if (!product.value || !requireLogin() || chatting.value) return
  chatting.value = true
  try {
    const conv = await api.conversations.open(product.value.id)
    await router.push({ path: '/chat', query: { cid: conv.id } })
  } catch (e) {
    toastError(e)
  } finally {
    chatting.value = false
  }
}
async function report() {
  if (!requireLogin()) return
  await router.push('/chat')
}

function openBuy() {
  if (!product.value || soldOut.value) return
  sheet.value = true
}

function preview(i: number) {
  if (!images.value.length) return
  showImagePreview({ images: images.value, startPosition: i, closeable: true })
}
function back() {
  goBack(router, '/')
}
</script>

<template>
  <div class="pdp">
    <van-nav-bar
      class="dock-top"
      :title="t('product.title')"
      left-arrow
      fixed
      placeholder
      safe-area-inset-top
      @click-left="back"
    />

    <template v-if="loading">
      <div class="skeleton gallery-skel" />
      <div class="section card body-skel">
        <div class="skeleton" style="height: 28px; width: 40%" />
        <div class="skeleton" style="height: 18px; margin-top: 12px" />
        <div class="skeleton" style="height: 18px; width: 70%; margin-top: 8px" />
      </div>
    </template>

    <van-empty v-else-if="notFound || !product" image="error" :description="t('errors.not_found')">
      <van-button round type="primary" to="/">{{ t('notFound.home') }}</van-button>
    </van-empty>

    <template v-else>
      <div class="gallery">
        <van-swipe v-if="images.length" :loop="false" :show-indicators="false" @change="slide = $event">
          <van-swipe-item v-for="(src, i) in images" :key="src">
            <button type="button" class="gallery-btn" :aria-label="t('product.imageN', { i: i + 1, n: images.length })" @click="preview(i)">
              <img :src="src" :alt="product.title" :loading="i === 0 ? 'eager' : 'lazy'" decoding="async" />
            </button>
          </van-swipe-item>
        </van-swipe>
        <ProductThumb v-else :src="null" :title="product.title" size="100%" :radius="0" />
        <span v-if="images.length > 1" class="gallery-count">{{ slide + 1 }}/{{ images.length }}</span>
      </div>

      <section class="section card info">
        <div class="price-row">
          <span v-if="price" class="price big"><small>{{ price.symbol }}</small>{{ price.value }}</span>
          <span class="muted">{{ t('product.soldCount', { n: formatNumber(product.sold, locale) }) }}</span>
        </div>
        <h1 class="title">{{ product.title }}</h1>
        <div class="facts">
          <span :class="isAuto ? 'badge-instant' : 'badge-manual'">{{ t(`deliveryMode.${product.delivery_mode}`) }}</span>
          <span v-if="isAuto && product.stock !== null" class="muted">
            {{ soldOut ? t('product.outOfStock') : t('product.stock', { n: product.stock }) }}
          </span>
        </div>
        <div v-if="as" class="facts"><span class="badge-as">{{ t(as.unit === 'h' ? 'product.afterSalesH' : 'product.afterSalesD', { n: as.n }) }}</span></div>
        <div v-else-if="product.after_sales_hours === 0" class="facts muted small">{{ t('delivery.noAfterSales') }}</div>
        <p v-if="labels.length" class="fmt">
          <strong>{{ t('product.deliveryFormat') }}</strong> {{ labels.join(' · ') }}
        </p>
        <p class="hint muted">{{ isAuto ? t('product.instantHint') : t('product.manualHint') }}</p>
      </section>

      <section class="section card ask">
        <span class="ask-q">{{ t('product.questions') }}</span>
        <div class="ask-btns">
          <button type="button" class="ask-btn" :disabled="chatting" @click="chat"><van-icon name="chat-o" aria-hidden="true" /> {{ t('product.chatUs') }}</button>
          <button type="button" class="ask-btn" @click="report"><van-icon name="warning-o" aria-hidden="true" /> {{ t('product.report') }}</button>
        </div>
      </section>

      <section class="section card desc">
        <h2>{{ t('product.description') }}</h2>
        <p class="desc-text">{{ product.description || t('product.noDescription') }}</p>
      </section>
      <section v-if="redeem || product.usage_html" class="section card desc">
        <h2>{{ t('delivery.howToUse') }}</h2>
        <a v-if="redeem" :href="redeem" target="_blank" rel="noopener noreferrer" class="redeem">{{ t('delivery.redeem') }} ↗</a>
        <SafeHtml v-if="product.usage_html" class="usage" :html="product.usage_html" />
      </section>
      <MiniStrip :title="t('product.related')" :items="related" />
      <div class="bar-spacer" />

      <van-action-bar class="dock-bar" safe-area-inset-bottom>
        <van-action-bar-icon icon="chat-o" :text="t('product.chat')" :disabled="chatting" @click="chat" />
        <van-action-bar-button
          type="primary"
          class="buy-btn"
          :text="soldOut ? t('product.outOfStock') : t('product.buyNow')"
          :disabled="soldOut"
          @click="openBuy"
        />
      </van-action-bar>

      <BuySheet v-model:show="sheet" :product="product" :qty="buyQty" :src="src" />
    </template>
  </div>
</template>

<style scoped>
.pdp {
  min-height: 100vh;
}
.gallery {
  position: relative;
  background: #f0eee8;
  aspect-ratio: 4 / 3;
  max-height: 280px;
  width: 100%;
  overflow: hidden;
}
.gallery :deep(.van-swipe),
.gallery :deep(.van-swipe-item) {
  height: 100%;
}
.gallery-btn {
  display: block;
  width: 100%;
  height: 100%;
  padding: 0;
  border: 0;
  background: none;
  cursor: zoom-in;
}
.gallery-btn img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.gallery-ph {
  display: grid;
  place-items: center;
  height: 100%;
  font-size: 64px;
  font-weight: 800;
  color: rgba(0, 0, 0, 0.1);
}
.gallery-count {
  position: absolute;
  right: 12px;
  bottom: 12px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 999px;
}
.gallery-skel {
  aspect-ratio: 4 / 3;
  max-height: 280px;
  border-radius: 0;
}
.body-skel {
  padding: 16px;
}
.info {
  padding: 14px 16px;
  margin-top: -12px;
  position: relative;
}
.price-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.price.big {
  font-size: 26px;
}
.price small {
  font-size: 15px;
  margin-right: 2px;
}
.title {
  font-size: 17px;
  line-height: 1.4;
  margin: 6px 0 8px;
  overflow-wrap: anywhere;
}
.facts {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
}
.hint {
  font-size: 12px;
  margin: 8px 0 0;
}
.ask {
  padding: 12px 16px;
}
.ask-q {
  font-size: 14px;
  font-weight: 600;
}
.ask-btns {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.ask-btn {
  flex: 1;
  min-height: 44px;
  border: 1px solid var(--c-line);
  border-radius: 999px;
  background: var(--c-surface);
  font: inherit;
  font-size: 14px;
  color: var(--c-text);
  cursor: pointer;
}
.badge-as {
  font-size: 11px;
  line-height: 18px;
  padding: 0 6px;
  border-radius: 4px;
  background: #e8f6ee;
  color: #17744a;
  font-weight: 600;
}
.fmt {
  margin: 8px 0 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.redeem {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  color: var(--c-price);
  font-weight: 600;
}
.usage {
  line-height: 1.6;
  color: var(--c-text-2);
  overflow-wrap: anywhere;
}
.desc {
  padding: 14px 16px;
}
.desc h2 {
  font-size: 15px;
  margin: 0 0 8px;
}
.desc-text {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--c-text-2);
  line-height: 1.7;
}
.bar-spacer {
  height: calc(64px + var(--safe-bottom));
}
.dock-bar {
  padding-right: 8px;
}
.dock-bar :deep(.buy-btn) {
  font-weight: 700;
}
.small {
  font-size: 12px;
}
</style>

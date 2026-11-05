<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { formatNumber, moneyParts, multiplyAmount, formatMoney, isApiError, type Product } from '@24goods/shared'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { showImagePreview, toastError } from '../ui'

const MAX_QTY = 10
const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const product = ref<Product | null>(null)
const loading = ref(true)
const notFound = ref(false)
const sheet = ref(false)
const qty = ref(1)
const submitting = ref(false)
const chatting = ref(false)
const slide = ref(0)

const id = computed(() => String(route.params.id ?? ''))
watch(
  id,
  async (v) => {
    if (!v) return
    loading.value = true
    notFound.value = false
    try {
      product.value = await api.product(v)
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
const maxQty = computed(() => {
  const p = product.value
  if (!p) return 1
  if (p.delivery_mode === 'auto_card' && p.stock !== null) return Math.max(1, Math.min(MAX_QTY, p.stock))
  return MAX_QTY
})
const total = computed(() => {
  const p = product.value
  if (!p) return ''
  return formatMoney(multiplyAmount(p.price, qty.value) ?? p.price, p.currency, locale.value)
})

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

function openBuy() {
  if (!product.value || soldOut.value || !requireLogin()) return
  qty.value = 1
  sheet.value = true
}

async function placeOrder() {
  if (!product.value || submitting.value) return
  submitting.value = true
  try {
    // Price is decided by the server; only product id + qty are sent.
    const order = await api.orders.create(product.value.id, qty.value)
    sheet.value = false
    await router.push(`/orders/${encodeURIComponent(order.id)}`)
  } catch (e) {
    toastError(e)
  } finally {
    submitting.value = false
  }
}

function preview(i: number) {
  if (!images.value.length) return
  showImagePreview({ images: images.value, startPosition: i, closeable: true })
}
function back() {
  if (window.history.length > 1) router.back()
  else void router.push('/')
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
        <div v-else class="gallery-ph" aria-hidden="true">24</div>
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
        <p class="hint muted">{{ isAuto ? t('product.instantHint') : t('product.manualHint') }}</p>
      </section>

      <button type="button" class="section card nego" @click="chat">
        <van-icon name="chat-o" size="18" />
        <span>{{ t('product.negotiate') }}</span>
        <van-icon name="arrow" class="muted" />
      </button>

      <section class="section card desc">
        <h2>{{ t('product.description') }}</h2>
        <p class="desc-text">{{ product.description || t('product.noDescription') }}</p>
      </section>
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

      <van-popup v-model:show="sheet" position="bottom" round closeable safe-area-inset-bottom class="buy-sheet" teleport="body">
        <div class="sheet">
          <div class="sheet-head">
            <img v-if="product.cover" :src="product.cover" alt="" class="sheet-cover" />
            <div>
              <div v-if="price" class="price big"><small>{{ price.symbol }}</small>{{ price.value }}</div>
              <div class="muted sheet-title">{{ product.title }}</div>
            </div>
          </div>
          <div class="sheet-row">
            <div>
              <div>{{ t('product.quantity') }}</div>
              <div class="muted small">{{ t('product.maxPerOrder', { n: maxQty }) }}</div>
            </div>
            <van-stepper v-model="qty" :min="1" :max="maxQty" integer :input-width="48" :button-size="36" />
          </div>
          <div class="sheet-row">
            <span>{{ t('common.total') }}</span>
            <span class="price">{{ total }}</span>
          </div>
          <van-button block round type="primary" size="large" :loading="submitting" @click="placeOrder">
            {{ t('product.placeOrder') }}
          </van-button>
        </div>
      </van-popup>
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
  aspect-ratio: 1 / 1;
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
  aspect-ratio: 1 / 1;
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
.nego {
  display: flex;
  align-items: center;
  gap: 8px;
  width: calc(100% - 24px);
  min-height: 48px;
  padding: 0 16px;
  border: 0;
  font: inherit;
  color: var(--c-text);
  cursor: pointer;
  text-align: start;
}
.nego span {
  flex: 1;
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
.sheet {
  padding: 20px 16px 16px;
}
.sheet-head {
  display: flex;
  gap: 12px;
  align-items: center;
  padding-right: 32px;
}
.sheet-cover {
  width: 72px;
  height: 72px;
  border-radius: 8px;
  object-fit: cover;
  flex: none;
}
.sheet-title {
  font-size: 13px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sheet-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 56px;
  border-bottom: 1px solid var(--c-line);
  margin-bottom: 4px;
}
.sheet-row:last-of-type {
  border-bottom: 0;
  margin-bottom: 16px;
}
.small {
  font-size: 12px;
}
</style>
<style>
/* the popup is teleported to body — keep it inside the app column */
.buy-sheet.van-popup--bottom {
  left: 0;
  right: 0;
  margin: 0 auto;
  max-width: var(--shell-w);
}
</style>

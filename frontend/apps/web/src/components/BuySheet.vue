<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ProductThumb } from '@24goods/shared/ui'
import {
  feeBreakdown,
  moneyParts,
  multiplyAmount,
  type BuyerInputConfig,
  type InstantCredentials,
  type ProductSummary,
} from '@24goods/shared'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { useSiteStore } from '../stores/site'
import BuyerInputSheet from './BuyerInputSheet.vue'
import FeeLines from './FeeLines.vue'
import ManualPromise from './ManualPromise.vue'
import PromoField, { type AppliedPromo } from './PromoField.vue'
import TurnstileBox from './TurnstileBox.vue'
import { recalledSrc, stashAccessCode } from '../lib/attribution'
import { usePow, isPowAbort } from '../composables/usePow'
import { useTurnstile } from '../composables/useTurnstile'
import CredentialsSheet from './CredentialsSheet.vue'
import PowStatus from './PowStatus.vue'
import { toastError } from '../ui'
import { isSoldOut, maxOrderQty } from '../lib/stock'
import { siteAccessCleared } from '../lib/securityChallenge'

const props = defineProps<{ show: boolean; product: (ProductSummary & { buyer_input?: BuyerInputConfig | null }) | null; qty?: number; src?: string | null }>()
const emit = defineEmits<{ 'update:show': [v: boolean] }>()
const { t, locale } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const site = useSiteStore()
const pow = usePow()
const ts = useTurnstile('instant_account')
const promo = ref<AppliedPromo | null>(null)

const qty = ref(1)
const submitting = ref(false)
const creds = ref<InstantCredentials | null>(null)
const showCreds = ref(false)
const showInput = ref(false)
/** The order-information value collected by the §4 popup for this purchase. */
const inputValue = ref<string | null>(null)

/**
 * The order-information form config. A summary without the field (an older cached list, a chat
 * card) is "unknown", not "off": fetch the product before any order can be placed (issue 49 B).
 */
const resolved = ref<{ id: string; config: BuyerInputConfig | null } | null>(null)
const resolving = ref(false)
const resolveFailed = ref(false)
const p = computed(() => {
  const x = props.product
  if (!x || x.buyer_input !== undefined) return x
  return resolved.value?.id === x.id ? { ...x, buyer_input: resolved.value.config } : x
})
const configKnown = computed(() => p.value?.buyer_input !== undefined)
async function resolveConfig() {
  const x = props.product
  if (!x || x.buyer_input !== undefined || resolving.value) return
  resolving.value = true
  resolveFailed.value = false
  try {
    const full = await api.product(x.id)
    if (props.product?.id === x.id) resolved.value = { id: x.id, config: full.buyer_input ?? null }
  } catch {
    resolveFailed.value = true
  } finally {
    resolving.value = false
  }
}
watch(siteAccessCleared, () => {
  if (props.show && resolveFailed.value) void resolveConfig()
})
const soldOut = computed(() => (p.value ? isSoldOut(p.value) : false))
const canSubmit = computed(() => configKnown.value && !soldOut.value)
const price = computed(() => (p.value ? moneyParts(p.value.price, p.value.currency, locale.value) : null))
const maxQty = computed(() => (p.value ? maxOrderQty(p.value) : 1))
const subtotal = computed(() => (p.value ? (multiplyAmount(p.value.price, qty.value) ?? p.value.price) : '0.00'))
const discountMinor = computed(() => (promo.value && !promo.value.stale ? promo.value.discount_minor : 0))
const bd = computed(() => feeBreakdown(subtotal.value, discountMinor.value, site.permille))
const promoItems = computed(() => (p.value ? [{ product_id: p.value.id, qty: qty.value }] : []))
const needsInput = computed(() => p.value?.buyer_input?.enabled === true)

watch(
  () => props.show,
  (v) => {
    if (v) {
      qty.value = Math.min(Math.max(1, Math.floor(props.qty ?? 1)), maxQty.value)
      inputValue.value = null
      promo.value = null
      void site.loadChannels()
      void resolveConfig()
    }
  },
)

function buyPath(): string {
  const id = encodeURIComponent(p.value?.id ?? '')
  const q = new URLSearchParams({ qty: String(qty.value) })
  const src = props.src ?? recalledSrc()
  if (src) q.set('src', src)
  return `/buy/${id}?${q.toString()}`
}
function signIn() {
  emit('update:show', false)
  void router.push({ name: 'login', query: { redirect: buyPath() } })
}

async function placeOrder() {
  const x = p.value
  if (!x || submitting.value || !canSubmit.value) return
  submitting.value = true
  try {
    // Price is decided by the server; only product id + qty (+ attribution tag) are sent.
    const order = await api.orders.create(x.id, qty.value, props.src ?? recalledSrc(), inputValue.value, promo.value?.code)
    stashAccessCode(order.id, order.access_code)
    emit('update:show', false)
    await router.push(`/orders/${encodeURIComponent(order.id)}`)
  } catch (e) {
    toastError(e)
  } finally {
    submitting.value = false
  }
}

/** Buy now: the order-information popup (if enabled) comes first, then the order is created. */
function begin(guest: boolean) {
  if (!canSubmit.value) return
  if (needsInput.value && inputValue.value === null) {
    pendingGuest = guest
    showInput.value = true
    return
  }
  void (guest ? continueAsGuest() : placeOrder())
}
let pendingGuest = false
function inputConfirmed(v: string) {
  inputValue.value = v
  showInput.value = false
  void (pendingGuest ? continueAsGuest() : placeOrder())
}

async function continueAsGuest() {
  if (submitting.value || pow.solving.value) return
  submitting.value = true
  try {
    const token = ts.require()
    const solution = await pow.run('instant')
    const r = await api.auth.instant(token ? { pow: solution, turnstile_token: token } : { pow: solution })
    auth.setUser(r.user)
    creds.value = r.credentials
    showCreds.value = true
  } catch (e) {
    if (!isPowAbort(e)) toastError(e)
  } finally {
    ts.reset()
    submitting.value = false
  }
}
async function credsDone() {
  showCreds.value = false
  creds.value = null // plaintext password leaves memory as soon as it was acknowledged
  await placeOrder()
}
</script>

<template>
  <van-popup
    :show="show && !showCreds && !showInput"
    position="bottom"
    round
    closeable
    safe-area-inset-bottom
    class="buy-sheet"
    teleport="body"
    @update:show="emit('update:show', $event)"
  >
    <div v-if="p" class="sheet">
      <div class="sheet-head">
        <ProductThumb :src="p.cover" :title="p.title" :size="72" :radius="8" />
        <div>
          <div v-if="price" class="price big"><small>{{ price.symbol }}</small>{{ price.value }}</div>
          <div class="muted sheet-title">{{ p.title }}</div>
        </div>
      </div>
      <div class="sheet-row">
        <div>
          <div>{{ t('product.quantity') }}</div>
          <div class="muted small">{{ t('product.maxPerOrder', { n: maxQty }) }}</div>
        </div>
        <van-stepper v-model="qty" :min="1" :max="maxQty" integer :input-width="48" :button-size="36" />
      </div>
      <ManualPromise v-if="p.delivery_mode === 'manual'" class="sheet-manual" :eta="p.delivery_eta_hours" compact />
      <p v-if="soldOut" class="muted small sheet-note" role="status">{{ t('product.outOfStock') }}</p>
      <p v-else-if="resolveFailed" class="sheet-note" role="alert">
        {{ t('buy.configFailed') }}
        <van-button size="small" plain round class="retry" @click="resolveConfig">{{ t('buy.retry') }}</van-button>
      </p>
      <PromoField v-model="promo" :items="promoItems" />
      <p v-if="promo?.stale" class="muted small">{{ t('promo.stale') }}</p>
      <div class="sheet-fees">
        <FeeLines
          :subtotal="subtotal"
          :discount-minor="discountMinor"
          :fee="bd.fee"
          :currency="p.currency"
          :label="site.channel?.label"
          :rate="site.channel?.fee_rate"
        />
      </div>

      <template v-if="auth.loggedIn">
        <van-button block round type="primary" size="large" :loading="submitting || resolving" :disabled="!canSubmit" @click="begin(false)">
          {{ t('product.placeOrder') }}
        </van-button>
      </template>
      <template v-else>
        <p v-if="site.features.guest_checkout" class="muted small guest-note">{{ t('buy.guestNote') }}</p>
        <PowStatus :solving="pow.solving.value" :progress="pow.progress.value" />
        <TurnstileBox v-if="site.features.guest_checkout" :ts="ts" />
        <van-button
          v-if="site.features.guest_checkout"
          block
          round
          type="primary"
          size="large"
          :loading="submitting || resolving"
          :disabled="!canSubmit"
          @click="begin(true)"
        >
          {{ t('buy.continueGuest') }}
        </van-button>
        <van-button block round :type="site.features.guest_checkout ? undefined : 'primary'" :plain="site.features.guest_checkout" size="large" class="signin" @click="signIn">{{ t('me.login') }}</van-button>
      </template>
    </div>
  </van-popup>
  <BuyerInputSheet v-model:show="showInput" :config="p?.buyer_input ?? null" :initial="inputValue" @confirm="inputConfirmed" />
  <CredentialsSheet :show="showCreds" :credentials="creds" @done="credsDone" />
</template>

<style scoped>
.sheet {
  padding: 20px 16px 16px;
}
.sheet-head {
  display: flex;
  gap: 12px;
  align-items: center;
  padding-right: 32px;
}
.price.big {
  font-size: 26px;
}
.price small {
  font-size: 15px;
  margin-right: 2px;
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
  margin-bottom: 12px;
}
.sheet-manual {
  padding: 8px 0;
}
.sheet-fees {
  padding: 12px 0;
}
.small {
  font-size: 12px;
}
.sheet-note {
  margin: 0 0 8px;
  font-size: 13px;
}
.retry {
  margin-left: 8px;
  min-height: 44px;
}
.guest-note {
  margin: 0 0 4px;
}
.signin {
  margin-top: 8px;
}
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ProductThumb } from '@24goods/shared/ui'
import {
  formatMoney,
  moneyParts,
  multiplyAmount,
  type InstantCredentials,
  type ProductSummary,
} from '@24goods/shared'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { recalledSrc, stashAccessCode } from '../lib/attribution'
import { usePow, isPowAbort } from '../composables/usePow'
import CredentialsSheet from './CredentialsSheet.vue'
import PowStatus from './PowStatus.vue'
import { toastError } from '../ui'

const MAX_QTY = 10
const props = defineProps<{ show: boolean; product: ProductSummary | null; qty?: number; src?: string | null }>()
const emit = defineEmits<{ 'update:show': [v: boolean] }>()
const { t, locale } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const pow = usePow()

const qty = ref(1)
const email = ref('')
const submitting = ref(false)
const creds = ref<InstantCredentials | null>(null)
const showCreds = ref(false)
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const p = computed(() => props.product)
const price = computed(() => (p.value ? moneyParts(p.value.price, p.value.currency, locale.value) : null))
const maxQty = computed(() => {
  const x = p.value
  if (!x) return 1
  if (x.delivery_mode === 'auto_card' && x.stock !== null) return Math.max(1, Math.min(MAX_QTY, x.stock))
  return MAX_QTY
})
const total = computed(() =>
  p.value ? formatMoney(multiplyAmount(p.value.price, qty.value) ?? p.value.price, p.value.currency, locale.value) : '',
)
const emailOk = computed(() => !email.value.trim() || EMAIL_RE.test(email.value.trim()))

watch(
  () => props.show,
  (v) => {
    if (v) qty.value = Math.min(Math.max(1, Math.floor(props.qty ?? 1)), maxQty.value)
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
  if (!x || submitting.value) return
  submitting.value = true
  try {
    // Price is decided by the server; only product id + qty (+ attribution tag) are sent.
    const order = await api.orders.create(x.id, qty.value, props.src ?? recalledSrc())
    stashAccessCode(order.id, order.access_code)
    emit('update:show', false)
    await router.push(`/orders/${encodeURIComponent(order.id)}`)
  } catch (e) {
    toastError(e)
  } finally {
    submitting.value = false
  }
}

async function continueAsGuest() {
  if (submitting.value || pow.solving.value || !emailOk.value) return
  submitting.value = true
  try {
    const solution = await pow.run('instant')
    const r = await api.auth.instant({ pow: solution, ...(email.value.trim() ? { email: email.value.trim() } : {}) })
    auth.setUser(r.user)
    creds.value = r.credentials
    showCreds.value = true
  } catch (e) {
    if (!isPowAbort(e)) toastError(e)
  } finally {
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
    :show="show && !showCreds"
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
      <div class="sheet-row">
        <span>{{ t('common.total') }}</span>
        <span class="price">{{ total }}</span>
      </div>

      <template v-if="auth.loggedIn">
        <van-button block round type="primary" size="large" :loading="submitting" @click="placeOrder">
          {{ t('product.placeOrder') }}
        </van-button>
      </template>
      <template v-else>
        <p class="muted small guest-note">{{ t('buy.guestNote') }}</p>
        <van-field
          v-model="email"
          type="email"
          inputmode="email"
          autocomplete="email"
          :label="t('auth.email')"
          :placeholder="t('buy.emailOptional')"
          :error-message="emailOk ? '' : t('auth.invalidEmail')"
        />
        <PowStatus :solving="pow.solving.value" :progress="pow.progress.value" />
        <van-button
          block
          round
          type="primary"
          size="large"
          :loading="submitting"
          :disabled="!emailOk"
          @click="continueAsGuest"
        >
          {{ t('buy.continueGuest') }}
        </van-button>
        <van-button block round plain size="large" class="signin" @click="signIn">{{ t('me.login') }}</van-button>
      </template>
    </div>
  </van-popup>
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
.small {
  font-size: 12px;
}
.guest-note {
  margin: 0 0 4px;
}
.signin {
  margin-top: 8px;
}
</style>

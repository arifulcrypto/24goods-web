<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ProductThumb } from '@24goods/shared/ui'
import {
  errorExtra,
  formatMoney,
  isApiError,
  safePayUrl,
  toMinor,
  type BuyerInputConfig,
  type InstantCredentials,
} from '@24goods/shared'
import { api } from '../api'
import BuyerInputSheet from '../components/BuyerInputSheet.vue'
import CredentialsSheet from '../components/CredentialsSheet.vue'
import FeeLines from '../components/FeeLines.vue'
import ManualPromise from '../components/ManualPromise.vue'
import PayChannelPicker from '../components/PayChannelPicker.vue'
import PromoField, { type AppliedPromo } from '../components/PromoField.vue'
import TurnstileBox from '../components/TurnstileBox.vue'
import PowStatus from '../components/PowStatus.vue'
import { isPowAbort } from '../composables/usePow'
import { useEnsureAccount } from '../composables/useEnsureAccount'
import { recalledSrc, stashAccessCode } from '../lib/attribution'
import { channelId } from '../lib/orderActions'
import { CART_MAX_QTY, useCartStore, type CartLine } from '../stores/cart'
import { useAuthStore } from '../stores/auth'
import { useSiteStore } from '../stores/site'
import { closeToast, confirm, showLoadingToast, showToast, toastError } from '../ui'

const { t, locale } = useI18n()
const router = useRouter()
const cart = useCartStore()
const auth = useAuthStore()
const site = useSiteStore()
const account = useEnsureAccount()

const busy = ref(false)
const creds = ref<InstantCredentials | null>(null)
const showCreds = ref(false)
const infoFor = ref<{ productId: string; config: BuyerInputConfig } | null>(null)
const showInfo = ref(false)
let infoResolve: ((v: string | null) => void) | null = null

onMounted(() => {
  void site.loadChannels()
  void cart.refreshQuote()
})

function toRow(l: CartLine) {
  const q = cart.quoteByProduct.get(l.key) ?? null
  const off = !!q && (!q.available || !!q.unavailable_reason)
  return { line: l, q, available: !off, loaded: !!q }
}
const rows = computed(() => cart.availableLines.map(toRow))
const offRows = computed(() => cart.unavailableLines.map(toRow))

// ---- v5 §1: manage mode (select all / delete selected) ----
const managing = ref(false)
const picked = ref<Set<string>>(new Set())
const allPicked = computed({
  get: () => cart.lines.length > 0 && picked.value.size === cart.lines.length,
  set: (v: boolean) => (picked.value = v ? new Set(cart.lines.map((l) => l.key)) : new Set()),
})
function togglePick(id: string, v: boolean) {
  const n = new Set(picked.value)
  if (v) n.add(id)
  else n.delete(id)
  picked.value = n
}
function toggleManage() {
  managing.value = !managing.value
  picked.value = new Set()
}
async function deletePicked() {
  const ids = [...picked.value]
  if (!ids.length) return
  if (!(await confirm(t('cart.deleteSelectedTitle', { n: ids.length }), ''))) return
  cart.removeMany(ids)
  picked.value = new Set()
  if (!cart.lines.length) managing.value = false
  void cart.refreshQuote()
}
async function clearUnavailable() {
  if (!(await confirm(t('cart.clearUnavailableTitle'), ''))) return
  cart.removeMany(cart.unavailableLines.map((l) => l.key))
  void cart.refreshQuote()
}

const allSelected = computed({
  get: () => cart.availableLines.length > 0 && cart.availableLines.every((l) => l.selected),
  set: (v: boolean) => cart.availableLines.forEach((l) => cart.setSelected(l.key, v)),
})

// ---- v5 §6: promo (preview via /promo/validate; the server recomputes at checkout) ----
const promo = ref<AppliedPromo | null>(null)
const promoItems = computed(() => cart.payable.map((l) => ({ product_id: l.product_id, qty: l.qty, ...(l.variant_id ? { variant_id: l.variant_id } : {}) })))
const discountMinor = computed(() => (promo.value && !promo.value.stale ? promo.value.discount_minor : 0))
const totals = computed(() => cart.totals(site.permille, discountMinor.value))
const discountApplied = computed(() => Number(toMinor(totals.value.discount) ?? 0n))
const currency = computed(() => cart.currency)
const groups = computed(() =>
  [
    { key: 'ok', rows: rows.value },
    { key: 'off', rows: offRows.value },
  ].filter((g) => g.rows.length),
)
function reasonText(r: string | null | undefined): string {
  return r === 'archived' ||
    r === 'unpublished' ||
    r === 'out_of_stock' ||
    r === 'variant_required' ||
    r === 'variant_unavailable'
    ? t(`cart.reason.${r}`)
    : t('cart.unavailable')
}

function maxQty(key: string): number {
  const q = cart.quoteByProduct.get(key)
  return q && q.max_qty > 0 ? Math.min(CART_MAX_QTY, q.max_qty) : CART_MAX_QTY
}
function onQty(key: string, v: number | string) {
  cart.setQty(key, Number(v))
  void cart.refreshQuote()
}
async function removeLine(key: string) {
  if (!(await confirm(t('cart.removeTitle'), ''))) return
  cart.remove(key)
  void cart.refreshQuote()
}

/** Ask for the §4 order information of a line that needs it. Resolves null when dismissed. */
function askInput(productId: string, config: BuyerInputConfig): Promise<string | null> {
  infoFor.value = { productId, config }
  showInfo.value = true
  return new Promise((resolve) => (infoResolve = resolve))
}
function infoConfirm(v: string) {
  showInfo.value = false
  infoResolve?.(v)
  infoResolve = null
}
function infoDismiss(v: boolean) {
  showInfo.value = v
  if (!v && infoResolve) {
    infoResolve(null)
    infoResolve = null
  }
}
async function collectMissingInputs(): Promise<boolean> {
  for (const l of cart.payable) {
    const q = cart.quoteByProduct.get(l.key)
    if (!q?.needs_input || l.buyer_input) continue
    const p = await api.product(l.product_id)
    if (!p.buyer_input?.enabled) continue
    const v = await askInput(l.product_id, p.buyer_input)
    if (v === null) return false
    cart.setInput(l.key, v)
  }
  return true
}
async function editInput(line: CartLine) {
  try {
    const p = await api.product(line.product_id)
    if (!p.buyer_input?.enabled) return
    infoFor.value = { productId: line.product_id, config: p.buyer_input }
    showInfo.value = true
    const v = await new Promise<string | null>((resolve) => (infoResolve = resolve))
    if (v !== null) cart.setInput(line.key, v)
  } catch (e) {
    toastError(e)
  }
}

async function checkout() {
  if (busy.value) return
  if (!cart.payableCount) {
    showToast(t('cart.nothingSelected'))
    return
  }
  if (!auth.loggedIn && !site.features.guest_checkout) {
    void router.push({ name: 'login', query: { redirect: '/cart' } })
    return
  }
  busy.value = true
  try {
    if (!(await collectMissingInputs())) return
    const wasGuest = !auth.loggedIn
    await account.ensure()
    if (wasGuest && auth.guestCreds) {
      // show the one-time credentials before paying, like the buy sheet does
      creds.value = auth.guestCreds
      showCreds.value = true
      return
    }
    await submit()
  } catch (e) {
    if (!isPowAbort(e)) toastError(e)
  } finally {
    busy.value = false
  }
}
async function credsDone() {
  showCreds.value = false
  creds.value = null
  busy.value = true
  try {
    await submit()
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}

async function submit() {
  const chosen = cart.payable.slice()
  showLoadingToast({ message: t('cart.working'), forbidClick: true, duration: 0 })
  let firstOrder: string | null = null
  let checkoutNo: string | null = null
  try {
    // Only product ids, qty and the order information are sent; the server prices everything.
    const r = await api.checkout.create(cart.toInputs(chosen), recalledSrc(), promo.value?.code)
    checkoutNo = r.checkout.checkout_no
    for (const o of r.orders) {
      stashAccessCode(o.id, r.access_codes[o.order_no])
      firstOrder ??= o.id
    }
    cart.removeMany(chosen.map((l) => l.key))
    const { pay_url } = await api.checkout.pay(checkoutNo, await channelId())
    const safe = safePayUrl(pay_url)
    if (!safe) throw new Error('unsafe pay_url')
    window.location.assign(safe)
  } catch (e) {
    closeToast()
    if (isApiError(e) && e.code.startsWith('promo_')) {
      promo.value = null // let the buyer re-apply or continue without the code
      toastError(e)
      return
    }
    if (isApiError(e) && e.code === 'out_of_stock') {
      toastError(e)
      void cart.refreshQuote()
      return
    }
    toastError(e)
    // the checkout exists: let the buyer pay from the order page
    if (checkoutNo && firstOrder) void router.push(`/orders/${encodeURIComponent(firstOrder)}`)
    else if (isApiError(e) && Object.keys(errorExtra(e)).length === 0) void cart.refreshQuote()
  }
}
</script>

<template>
  <div class="page with-footer">
    <van-nav-bar :title="t('cart.title')" class="dock-top" fixed placeholder safe-area-inset-top>
      <template v-if="cart.lines.length" #right>
        <button type="button" class="manage" @click="toggleManage">{{ managing ? t('cart.done') : t('cart.manage') }}</button>
      </template>
    </van-nav-bar>

    <van-empty v-if="!cart.lines.length" image="default" :description="t('cart.empty')">
      <van-button round type="primary" to="/">{{ t('cart.goShopping') }}</van-button>
    </van-empty>

    <template v-else>
      <button v-if="cart.quoteError && !cart.quote" type="button" class="retry" @click="cart.refreshQuote()">
        {{ t('cart.refreshFailed') }}
      </button>
      <template v-for="g in groups" :key="g.key">
        <div v-if="g.key === 'off'" class="off-head">
          <span>{{ t('cart.unavailableGroup', { n: g.rows.length }) }}</span>
          <button type="button" class="clear" @click="clearUnavailable">{{ t('cart.clearUnavailable') }}</button>
        </div>
        <ul class="lines" :class="{ 'lines-off': g.key === 'off' }">
          <li v-for="r in g.rows" :key="r.line.key">
            <van-swipe-cell class="card">
              <div class="line" :class="{ off: !r.available }">
                <van-checkbox
                  v-if="managing"
                  :model-value="picked.has(r.line.key)"
                  class="chk"
                  :aria-label="r.q?.title ?? ''"
                  @update:model-value="togglePick(r.line.key, $event)"
                />
                <van-checkbox
                  v-else
                  :model-value="r.line.selected && r.available"
                  :disabled="!r.available"
                  class="chk"
                  :aria-label="r.q?.title ?? ''"
                  @update:model-value="cart.setSelected(r.line.key, $event)"
                />
                <RouterLink :to="`/p/${encodeURIComponent(r.line.product_id)}`" class="thumb">
                  <ProductThumb :src="r.q?.cover ?? null" :title="r.q?.title ?? ''" :size="72" :radius="8" />
                </RouterLink>
                <div class="info">
                  <div class="title-row">
                    <div class="title">{{ r.q?.title ?? '…' }}</div>
                    <button type="button" class="trash" :aria-label="t('cart.remove')" @click="removeLine(r.line.key)">
                      <van-icon name="delete-o" size="20" />
                    </button>
                  </div>
                  <div v-if="r.q?.variant_name" class="muted small variant-name">{{ r.q.variant_name }}</div>
                  <div v-if="!r.available && r.loaded" class="flag">{{ reasonText(r.q?.unavailable_reason) }}</div>
                  <template v-else-if="r.q">
                    <div class="muted small">
                      {{ t('cart.unitPrice', { price: formatMoney(r.q.unit_price, r.q.currency, locale) }) }}
                    </div>
                    <ManualPromise v-if="r.q.delivery_mode === 'manual'" class="small" :eta="r.q.delivery_eta_hours" compact />
                  </template>
                  <button v-if="r.q?.needs_input && r.available" type="button" class="link" @click="editInput(r.line)">
                    {{ r.line.buyer_input ? t('cart.infoSet') + ' · ' + t('cart.editInfo') : t('cart.editInfo') }}
                  </button>
                  <div class="bottom">
                    <span v-if="r.q" class="price">{{ formatMoney(cart.lineSubtotal(r.line, r.q), r.q.currency, locale) }}</span>
                    <van-stepper
                      :model-value="r.line.qty"
                      :min="1"
                      :max="maxQty(r.line.key)"
                      :disabled="!r.available"
                      integer
                      :input-width="40"
                      :button-size="32"
                      @change="onQty(r.line.key, $event)"
                    />
                  </div>
                </div>
              </div>
              <template #right>
                <van-button square type="danger" class="del" :text="t('cart.remove')" @click="removeLine(r.line.key)" />
              </template>
            </van-swipe-cell>
            <p v-if="!r.available && r.loaded" class="muted small hint">{{ t('cart.unavailableHint') }}</p>
          </li>
        </ul>
      </template>
      <div v-if="!managing && cart.payableCount" class="card summary">
        <PromoField v-model="promo" :items="promoItems" />
        <p v-if="promo?.stale" class="muted small stale">{{ t('promo.stale') }}</p>
        <PayChannelPicker />
        <FeeLines
          :subtotal="totals.subtotal"
          :discount-minor="discountApplied"
          :fee="totals.fee"
          :currency="currency"
          :label="site.channel?.label"
          :rate="site.channel?.fee_rate"
          hide-when-free
        />
      </div>
      <div v-if="!managing && !auth.loggedIn && cart.payableCount" class="summary"><TurnstileBox :ts="account.ts" /></div>
      <PowStatus :solving="account.solving.value" :progress="account.progress.value" />

      <div v-if="managing" class="dock footer">
        <van-checkbox v-model="allPicked" class="all">{{ t('cart.selectAll') }}</van-checkbox>
        <div class="sum" />
        <van-button round type="danger" class="go" :disabled="!picked.size" @click="deletePicked">
          {{ t('cart.deleteN', { n: picked.size }) }}
        </van-button>
      </div>
      <div v-else class="dock footer">
        <van-checkbox v-model="allSelected" class="all">{{ t('cart.selectAll') }}</van-checkbox>
        <div class="sum">
          <span class="muted small">{{ t('cart.total') }}</span>
          <span class="price big">{{ formatMoney(totals.total, currency, locale) }}</span>
        </div>
        <van-button round type="primary" class="go" :loading="busy" :disabled="!cart.payableCount" @click="checkout">
          {{ t('cart.checkout', { n: cart.payableCount }) }}
        </van-button>
      </div>
    </template>

    <BuyerInputSheet
      :show="showInfo"
      :config="infoFor?.config ?? null"
      @update:show="infoDismiss"
      @confirm="infoConfirm"
    />
    <CredentialsSheet :show="showCreds" :credentials="creds" @done="credsDone" />
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
}
.with-footer {
  padding-bottom: calc(var(--tabbar-h) + var(--safe-bottom) + 72px);
}
.lines {
  list-style: none;
  margin: 0;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.card {
  overflow: hidden;
}
.line {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 12px;
}
.line.off {
  opacity: 0.6;
}
.chk {
  flex: none;
  min-width: 28px;
  min-height: 44px;
}
.thumb {
  flex: none;
  display: block;
}
.info {
  flex: 1;
  min-width: 0;
}
.title {
  font-weight: 600;
  font-size: 14px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.flag {
  display: inline-block;
  margin-top: 4px;
  font-size: 12px;
  padding: 0 6px;
  border-radius: 4px;
  background: #fdecec;
  color: #c0392b;
}
.bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
  gap: 8px;
}
.link {
  min-height: 44px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--c-price);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  text-align: start;
}
.del {
  height: 100%;
  min-width: 72px;
}
.hint {
  margin: 4px 4px 0;
}
.small {
  font-size: 12px;
}
.summary {
  margin: 0 12px;
  padding: 12px 14px;
}
.retry {
  display: block;
  margin: 12px auto;
  min-height: 44px;
  border: 0;
  background: none;
  color: var(--c-price);
  font: inherit;
  cursor: pointer;
}
.footer {
  bottom: calc(var(--tabbar-h) + var(--safe-bottom));
  z-index: 49;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--c-surface);
  box-shadow: 0 -1px 0 var(--c-line);
}
.all {
  flex: none;
  min-height: 44px;
  font-size: 13px;
}
.sum {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: 1.2;
}
.price.big {
  font-size: 18px;
}
.go {
  flex: none;
  min-width: 120px;
  font-weight: 700;
}
.manage {
  min-height: 44px;
  min-width: 44px;
  padding: 0 4px;
  border: 0;
  background: none;
  color: var(--c-text);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
.title-row {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}
.title-row .title {
  flex: 1;
  min-width: 0;
}
.trash {
  flex: none;
  width: 44px;
  height: 44px;
  margin: -10px -10px 0 0;
  border: 0;
  background: none;
  color: var(--c-muted, #8a8a8a);
  display: grid;
  place-items: center;
  cursor: pointer;
}
.off-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px 0;
  font-size: 13px;
  color: var(--c-muted, #8a8a8a);
}
.clear {
  min-height: 44px;
  padding: 0 4px;
  border: 0;
  background: none;
  color: var(--c-price);
  font: inherit;
  cursor: pointer;
}
.lines-off {
  padding-top: 4px;
}
.stale {
  margin: 4px 0 8px;
}
</style>

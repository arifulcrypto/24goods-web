<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  copyText,
  effectiveQuoteStatus,
  formatDateTime,
  formatMoney,
  isValidAmount,
  latestQuotes,
  normalizeAmount,
  type AdminOrder,
  type Conversation,
  type Message,
  type QuoteCardPayload,
} from '@24goods/shared'
import { ProductCardView } from '@24goods/shared/chat'
import { api } from '../api'
import type { OrderRowV4 } from '../api-v4'
import OrderStatusTag from './OrderStatusTag.vue'
import { toastError, toastOk } from '../ui'

const props = defineProps<{ conversation: Conversation; messages: Message[]; withdrawing?: string | null }>()
const emit = defineEmits<{ quoteSent: []; withdraw: [q: QuoteCardPayload]; openOrder: [id: string] }>()
const { t, locale } = useI18n()

const orders = ref<(AdminOrder & Partial<OrderRowV4>)[]>([])
const inputOrder = computed(() => orders.value.find((o) => o.buyer_input) ?? null)
async function copyInput() {
  const v = inputOrder.value?.buyer_input
  if (!v) return
  if (await copyText(v)) toastOk(t('common.copied'))
  else toastError(new Error('copy'))
}
const ordersLoading = ref(false)
const form = reactive({ title: '', amount: '', qty: 1, note: '', expires: 30, busy: false, error: '' })

async function loadOrders() {
  const buyerId = props.conversation.buyer.id
  ordersLoading.value = true
  try {
    // `buyer_id` is a proposed filter; results are also filtered client-side to be safe.
    const r = await api.orders.list({ buyer_id: buyerId, page: 1, page_size: 10 })
    orders.value = r.items.filter((o) => o.buyer?.id === buyerId).slice(0, 5)
  } catch {
    orders.value = []
  } finally {
    ordersLoading.value = false
  }
}
watch(
  () => props.conversation.id,
  () => {
    form.title = props.conversation.product?.title ?? ''
    form.amount = ''
    form.qty = 1
    form.note = ''
    form.expires = 30
    form.error = ''
    void loadOrders()
  },
  { immediate: true },
)
watch(
  () => props.conversation.product?.title,
  (title) => {
    if (title && !form.title) form.title = title
  },
)

const quotes = computed(() => latestQuotes(props.messages))

async function sendQuote() {
  if (form.busy) return
  form.error = ''
  if (!form.title.trim()) {
    form.error = t('common.required')
    return
  }
  if (!isValidAmount(form.amount)) {
    form.error = t('products.v.priceInvalid')
    return
  }
  form.busy = true
  try {
    await api.conversations.sendQuote(props.conversation.id, {
      title: form.title.trim(),
      amount: normalizeAmount(form.amount)!,
      qty: form.qty,
      note: form.note.trim() || undefined,
      product_id: props.conversation.product?.product_id,
      expires_in_minutes: form.expires,
    })
    toastOk(t('inbox.quoteSent'))
    form.amount = ''
    form.note = ''
    emit('quoteSent')
  } catch (e) {
    toastError(e)
  } finally {
    form.busy = false
  }
}

defineExpose({ reloadOrders: loadOrders })
</script>

<template>
  <div class="side">
    <section class="block">
      <h3>{{ t('inbox.buyer') }}</h3>
      <div class="buyer">
        <el-avatar :size="36">{{ conversation.buyer.display_name.charAt(0).toUpperCase() }}</el-avatar>
        <div>
          <div class="name">{{ conversation.buyer.display_name }}</div>
          <div class="muted small mono">{{ conversation.buyer.id }}</div>
        </div>
      </div>
    </section>

    <section class="block">
      <h3>{{ t('inbox.product') }}</h3>
      <ProductCardView v-if="conversation.product" :product="conversation.product" compact @open="() => undefined" />
      <div v-else class="muted small">{{ t('inbox.noProduct') }}</div>
    </section>

    <section class="block">
      <h3>{{ t('inbox.sendQuote') }}</h3>
      <el-form label-position="top" size="small" @submit.prevent="sendQuote">
        <el-form-item :label="t('inbox.quoteTitle')">
          <el-input v-model="form.title" maxlength="120" />
        </el-form-item>
        <div class="two">
          <el-form-item :label="t('inbox.amount')">
            <el-input v-model="form.amount" inputmode="decimal" placeholder="0.00" />
          </el-form-item>
          <el-form-item :label="t('inbox.qty')">
            <el-input-number v-model="form.qty" :min="1" :max="100" controls-position="right" style="width: 100%" />
          </el-form-item>
        </div>
        <el-form-item :label="t('inbox.note')">
          <el-input v-model="form.note" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
        <el-form-item :label="t('inbox.expiresIn')" :error="form.error">
          <el-input-number v-model="form.expires" :min="5" :max="1440" :step="5" controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="form.busy" :disabled="conversation.status === 'closed'" style="width: 100%">
          {{ t('inbox.sendQuote') }}
        </el-button>
      </el-form>
    </section>

    <section class="block">
      <h3>{{ t('inbox.quotes') }}</h3>
      <div v-if="!quotes.length" class="muted small">{{ t('inbox.noQuotes') }}</div>
      <div v-for="q in quotes" :key="q.quote_id" class="quote">
        <div class="quote-main">
          <div class="quote-title">{{ q.title }}</div>
          <div class="small">
            <span class="price">{{ formatMoney(q.amount, q.currency, locale) }}</span>
            <span class="muted"> × {{ q.qty }} · {{ formatDateTime(q.expires_at, locale) }}</span>
          </div>
        </div>
        <el-tag size="small" :type="effectiveQuoteStatus(q) === 'offered' ? 'warning' : effectiveQuoteStatus(q) === 'accepted' ? 'success' : 'info'">
          {{ t(`quoteStatus.${effectiveQuoteStatus(q)}`) }}
        </el-tag>
        <el-button
          v-if="effectiveQuoteStatus(q) === 'offered'"
          link
          type="danger"
          size="small"
          :loading="withdrawing === q.quote_id"
          @click="emit('withdraw', q)"
        >
          {{ t('chat.quote.withdraw') }}
        </el-button>
        <el-button v-if="q.order_id" link type="primary" size="small" @click="emit('openOrder', q.order_id)">
          {{ t('chat.quote.viewOrder') }}
        </el-button>
      </div>
    </section>

    <section v-if="inputOrder" class="block">
      <h3>{{ t('inbox.buyerInput') }}</h3>
      <div class="muted small mono">{{ inputOrder.order_no }}</div>
      <p class="pre-wrap mono">{{ inputOrder.buyer_input }}</p>
      <el-button size="small" @click="copyInput">{{ t('orders.copy') }}</el-button>
    </section>

    <section class="block">
      <h3>{{ t('inbox.recentOrders') }}</h3>
      <div v-loading="ordersLoading">
        <div v-if="!orders.length && !ordersLoading" class="muted small">{{ t('inbox.noOrders') }}</div>
        <button v-for="o in orders" :key="o.id" type="button" class="order" @click="emit('openOrder', o.id)">
          <span class="order-title">{{ o.title }}</span>
          <span class="order-meta">
            <OrderStatusTag :status="o.status" />
            <span class="price small">{{ formatMoney(o.amount, o.currency, locale) }}</span>
          </span>
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.side {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.block {
  background: var(--a-surface);
  border: 1px solid var(--a-line);
  border-radius: 10px;
  padding: 12px;
}
h3 {
  font-size: 13px;
  margin: 0 0 10px;
  color: var(--a-muted);
  font-weight: 600;
}
.buyer {
  display: flex;
  gap: 10px;
  align-items: center;
}
.name {
  font-weight: 600;
}
.small {
  font-size: 12px;
}
.pre-wrap {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 160px;
  overflow: auto;
  margin: 6px 0;
  font-size: 12px;
}
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.quote {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 0;
  border-top: 1px solid var(--a-line);
}
.quote:first-of-type {
  border-top: 0;
}
.quote-main {
  flex: 1;
  min-width: 0;
}
.quote-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.order {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 8px;
  border: 1px solid var(--a-line);
  border-radius: 8px;
  background: #fafafa;
  margin-bottom: 6px;
  text-align: start;
  cursor: pointer;
  font: inherit;
}
.order-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.order-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>

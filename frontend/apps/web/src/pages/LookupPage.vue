<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { DeliveryView } from '@24goods/shared/ui'
import {
  addLookup,
  formatDateTime,
  formatMoney,
  getLookups,
  goBack,
  isApiError,
  revealedCodes,
  type LookupOrder,
} from '@24goods/shared'
import { api } from '../api'
import PowStatus from '../components/PowStatus.vue'
import { isPowAbort, usePow } from '../composables/usePow'
import { confirm, toastError } from '../ui'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const pow = usePow()

const orderNo = ref(typeof route.query.no === 'string' ? route.query.no.trim().slice(0, 64) : '')
const code = ref('')
const busy = ref(false)
const failed = ref('')
const order = ref<LookupOrder | null>(null)
const codes = ref<string[]>([])
const revealing = ref(false)
const recent = ref(getLookups())
// the access code lives only in memory, and only while a result is on screen
let usedCode = ''

const valid = computed(() => orderNo.value.trim().length >= 4 && code.value.trim().length >= 6)

function normCode(s: string): string {
  return s.replace(/[\s-]/g, '').toUpperCase()
}

async function find() {
  if (!valid.value || busy.value) return
  busy.value = true
  failed.value = ''
  codes.value = []
  try {
    const solution = await pow.run('lookup')
    const no = orderNo.value.trim()
    const ac = normCode(code.value)
    order.value = await api.lookup.find({ order_no: no, access_code: ac, pow: solution })
    usedCode = ac
    recent.value = addLookup(no)
  } catch (e) {
    order.value = null
    if (isPowAbort(e)) return
    if (isApiError(e) && (e.code === 'not_found' || e.code === 'forbidden' || e.code === 'validation')) failed.value = t('lookup.notFound')
    else toastError(e)
  } finally {
    busy.value = false
  }
}

async function reveal() {
  const o = order.value
  if (!o || revealing.value) return
  if (!(await confirm(t('delivery.revealTitle'), t('delivery.revealWarn')))) return
  revealing.value = true
  try {
    codes.value = revealedCodes(await api.lookup.reveal({ order_no: o.order_no, access_code: usedCode }))
  } catch (e) {
    toastError(e)
  } finally {
    revealing.value = false
  }
}
function reset() {
  order.value = null
  codes.value = []
  usedCode = ''
  code.value = ''
}
onBeforeUnmount(reset)
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('lookup.title')" class="dock-top" left-arrow fixed placeholder safe-area-inset-top @click-left="goBack(router, '/')" />

    <form v-if="!order" class="section card pad" @submit.prevent="find">
      <p class="muted intro">{{ t('lookup.intro') }}</p>
      <label class="lbl" for="lk-no">{{ t('lookup.orderNo') }}</label>
      <input id="lk-no" v-model="orderNo" class="inp" autocomplete="off" autocapitalize="characters" maxlength="64" :placeholder="t('lookup.orderNoHint')" />
      <div v-if="recent.length" class="recent" role="group" :aria-label="t('lookup.recent')">
        <button v-for="r in recent" :key="r" type="button" class="rchip" @click="orderNo = r">{{ r }}</button>
      </div>
      <label class="lbl" for="lk-code">{{ t('lookup.accessCode') }}</label>
      <input id="lk-code" v-model="code" class="inp mono" autocomplete="off" autocapitalize="characters" maxlength="24" :placeholder="t('lookup.accessCodeHint')" />
      <p v-if="failed" class="err" role="alert">{{ failed }}</p>
      <PowStatus :solving="pow.solving.value" :progress="pow.progress.value" />
      <van-button block round type="primary" size="large" native-type="submit" :loading="busy" :disabled="!valid">
        {{ t('lookup.submit') }}
      </van-button>
    </form>

    <template v-else>
      <section class="section card pad">
        <div class="o-title">{{ order.title }}</div>
        <div class="row">
          <span class="muted">{{ t('order.orderNo') }}</span><code>{{ order.order_no }}</code>
        </div>
        <div class="row">
          <span class="muted">{{ t('common.status') }}</span><strong>{{ t(`orderStatus.${order.status}`) }}</strong>
        </div>
        <div class="row">
          <span class="muted">{{ t('order.amount') }}</span><span class="price">{{ formatMoney(order.amount, order.currency, locale) }}</span>
        </div>
        <div class="row">
          <span class="muted">{{ t('order.createdAt') }}</span><span>{{ formatDateTime(order.created_at, locale) }}</span>
        </div>
        <div v-if="order.delivered_at" class="row">
          <span class="muted">{{ t('order.deliveredAt') }}</span><span>{{ formatDateTime(order.delivered_at, locale) }}</span>
        </div>
      </section>
      <section v-if="order.can_reveal || codes.length || order.manual_delivery?.text" class="section card pad">
        <DeliveryView
          :info="order.delivery_info"
          :codes="codes"
          :can-reveal="!!order.can_reveal"
          :revealing="revealing"
          :manual-text="order.manual_delivery?.text ?? null"
          :order-no="order.order_no"
          :after-sales-until="order.after_sales_until"
          @reveal="reveal"
          @hide="codes = []"
        />
      </section>
      <p class="muted small hint">{{ t('lookup.signInForMore') }}</p>
      <div class="actions">
        <van-button round plain @click="reset">{{ t('lookup.another') }}</van-button>
      </div>
    </template>
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
.intro {
  font-size: 13px;
  margin: 0 0 8px;
}
.lbl {
  display: block;
  font-size: 13px;
  font-weight: 600;
  margin: 12px 0 4px;
}
.inp {
  width: 100%;
  box-sizing: border-box;
  min-height: 44px;
  border: 1px solid var(--c-line);
  border-radius: 10px;
  padding: 0 12px;
  font: inherit;
  font-size: 15px;
  background: var(--c-surface);
}
.inp.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  letter-spacing: 0.06em;
}
.recent {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.rchip {
  min-height: 44px;
  padding: 0 12px;
  border: 0;
  border-radius: 999px;
  background: #f2f2f2;
  font: inherit;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  cursor: pointer;
}
.err {
  color: var(--c-danger);
  font-size: 13px;
  margin: 10px 0 0;
}
.o-title {
  font-weight: 700;
  font-size: 16px;
  overflow-wrap: anywhere;
  margin-bottom: 6px;
}
.row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  min-height: 32px;
  align-items: center;
  font-size: 14px;
}
.row code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow-wrap: anywhere;
  text-align: end;
}
.hint {
  margin: 8px 16px;
  font-size: 12px;
}
.actions {
  display: flex;
  justify-content: center;
  margin: 12px;
}
</style>

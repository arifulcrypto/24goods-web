<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TestPaymentView } from '@24goods/shared'
import { client } from '../../api'
import { toastError } from '../../ui'
import { formatCountdown, shouldPoll, testPhase, validTestAmount } from '../../lib/gatewayRouting'

// Test payment (review-v10 §C): a real 0.01..10.00 CNY payment at one gateway, paid by scanning the
// QR with the payee app. The QR is an SVG rendered by the server (same renderer as the USDT pay
// page): no client-side QR library and no CDN. The server re-queries the gateway at most every 3 s.
const props = defineProps<{ gatewayId: string | null; label: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'done'): void }>()
const { t } = useI18n()
const amount = ref('0.01')
const view = ref<TestPaymentView | null>(null)
const busy = ref(false)
const left = ref(0)
let poll: ReturnType<typeof setInterval> | undefined
let tick: ReturnType<typeof setInterval> | undefined

const phase = computed(() => testPhase(view.value, left.value))
const qr = computed(() => (view.value?.qr_svg ? `data:image/svg+xml;base64,${btoa(view.value.qr_svg)}` : ''))
const amountOk = computed(() => validTestAmount(amount.value))

function stop() {
  if (poll) clearInterval(poll)
  if (tick) clearInterval(tick)
  poll = tick = undefined
}
function apply(v: TestPaymentView) {
  view.value = v
  left.value = v.seconds_left
  if (v.status !== 'pending') { stop(); emit('done') }
}
async function refresh() {
  if (!view.value || !shouldPoll(phase.value)) return
  try { apply(await client.get<TestPaymentView>(`/admin/payment-gateways/test-payments/${view.value.id}`)) } catch { /* next tick retries */ }
}
async function start() {
  if (!props.gatewayId || !amountOk.value || busy.value) return
  busy.value = true
  stop()
  view.value = null
  try {
    const v = await client.post<TestPaymentView>(`/admin/payment-gateways/${encodeURIComponent(props.gatewayId)}/test-payment`, { amount: amount.value.trim() })
    apply(v)
    if (v.status === 'pending') {
      tick = setInterval(() => { left.value = Math.max(0, left.value - 1) }, 1000)
      poll = setInterval(refresh, 3000)
    }
  } catch (e) { toastError(e) } finally { busy.value = false }
}
function close() { stop(); view.value = null; amount.value = '0.01'; emit('close') }
watch(() => props.gatewayId, () => { stop(); view.value = null })
onBeforeUnmount(stop)
</script>
<template>
  <el-dialog :model-value="!!gatewayId" :title="t('gwRouting.testTitle', { label })" width="min(92vw, 460px)" @close="close">
    <p class="muted small">{{ t('gwRouting.testNote') }}</p>
    <el-form v-if="!view" label-position="top" @submit.prevent="start">
      <el-form-item :label="t('gwRouting.amount')" :error="amountOk ? '' : t('gwRouting.amountHint')">
        <el-input v-model="amount" inputmode="decimal" maxlength="5" />
      </el-form-item>
      <el-button type="primary" native-type="submit" :disabled="!amountOk" :loading="busy">{{ t('gwRouting.start') }}</el-button>
    </el-form>
    <div v-else class="tp" data-testid="test-payment">
      <img v-if="qr && phase === 'waiting'" class="tp-qr" :src="qr" :alt="t('gwRouting.qrAlt')" width="200" height="200" />
      <dl class="tp-meta">
        <dt>{{ t('gwRouting.gateway') }}</dt><dd>{{ view.gateway_label }}</dd>
        <dt>{{ t('gwRouting.amount') }}</dt><dd>¥{{ view.amount }}</dd>
        <dt>{{ t('gwRouting.outTradeNo') }}</dt><dd><code>{{ view.out_trade_no }}</code></dd>
        <template v-if="phase === 'waiting'"><dt>{{ t('gwRouting.expiresIn') }}</dt><dd>{{ formatCountdown(left) }}</dd></template>
      </dl>
      <el-alert v-if="phase === 'waiting' && !qr && view.reason" :title="t(`gwRouting.reasons.${view.reason}`)" type="warning" :closable="false" show-icon />
      <el-alert v-else-if="phase === 'waiting'" :title="t('gwRouting.waiting')" type="info" :closable="false" show-icon />
      <el-alert v-else-if="phase === 'paid'" :title="t('gwRouting.paid')" type="success" :closable="false" show-icon />
      <el-alert v-else-if="phase === 'expired'" :title="t('gwRouting.expired')" type="warning" :closable="false" show-icon />
      <el-alert v-else :title="t(`gwRouting.reasons.${view.reason ?? 'gateway_unknown'}`)" :description="view.message ?? ''" type="error" :closable="false" show-icon />
      <p v-if="view.redirect_url && phase === 'waiting'" class="small"><a :href="view.redirect_url" target="_blank" rel="noopener noreferrer">{{ t('gwRouting.openCashier') }}</a></p>
      <p class="muted small">{{ t('gwRouting.refundNote') }}</p>
      <el-button v-if="phase !== 'waiting'" @click="view = null">{{ t('gwRouting.again') }}</el-button>
    </div>
  </el-dialog>
</template>
<style scoped>
.tp { display: grid; gap: 12px; justify-items: center }
.tp-qr { background: #fff; padding: 8px; border-radius: 8px; image-rendering: pixelated }
.tp-meta { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; width: 100%; margin: 0 }
.tp-meta dt { color: var(--el-text-color-secondary) }
.tp-meta dd { margin: 0; word-break: break-all }
.tp :deep(.el-alert) { width: 100% }
</style>

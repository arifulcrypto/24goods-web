<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatMoney, fromMinor, toMinor } from '@24goods/shared'

/** Subtotal / Payment fee (Alipay 3%) / Total. Display only: the server owns the numbers. */
const props = defineProps<{
  subtotal: string
  /** v5 §6: promo discount in minor units (subtotal and fee are as the server/preview computed them). */
  discountMinor?: number
  fee: string
  currency: string
  /** channel label (e.g. "Alipay") */
  label?: string | null
  /** e.g. "3%" */
  rate?: string | null
  /** hide the subtotal/fee rows when there is no fee */
  hideWhenFree?: boolean
}>()
const { t, locale } = useI18n()
const hasFee = computed(() => (toMinor(props.fee) ?? 0n) > 0n)
const discount = computed(() => BigInt(Math.max(0, Math.floor(props.discountMinor ?? 0))))
const hasDiscount = computed(() => discount.value > 0n)
const total = computed(() => {
  const s = toMinor(props.subtotal)
  const f = toMinor(props.fee)
  return s === null || f === null ? props.subtotal : fromMinor(s - discount.value + f)
})
const feeLabel = computed(() =>
  props.label && props.rate ? t('fee.line', { label: props.label, rate: props.rate }) : t('fee.lineNoLabel'),
)
const money = (v: string) => formatMoney(v, props.currency, locale.value)
</script>

<template>
  <div class="fee-lines">
    <div v-if="hasFee || hasDiscount || !hideWhenFree" class="fl-row">
      <span class="muted">{{ t('fee.subtotal') }}</span>
      <span>{{ money(subtotal) }}</span>
    </div>
    <div v-if="hasDiscount" class="fl-row">
      <span class="muted">{{ t('fee.discount') }}</span>
      <span class="disc">−{{ money(fromMinor(discount)) }}</span>
    </div>
    <div v-if="hasFee" class="fl-row">
      <span class="muted">{{ feeLabel }}</span>
      <span>{{ money(fee) }}</span>
    </div>
    <div class="fl-row fl-total">
      <span>{{ t('fee.total') }}</span>
      <span class="price">{{ money(total) }}</span>
    </div>
  </div>
</template>

<style scoped>
.fee-lines {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 14px;
}
.fl-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}
.disc {
  color: var(--c-success, #07a35a);
}
.fl-total {
  font-weight: 700;
  font-size: 16px;
  margin-top: 2px;
}
</style>

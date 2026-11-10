<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ProductThumb } from '@24goods/shared/ui'
import { formatNumber, moneyParts, type ProductSummary } from '@24goods/shared'
import { afterSalesLabel } from '../lib/afterSales'
import { isSoldOut } from '../lib/stock'

const props = defineProps<{ product: ProductSummary }>()
const emit = defineEmits<{ buy: [p: ProductSummary] }>()
const { t, locale } = useI18n()
const price = computed(() => moneyParts(props.product.price, props.product.currency, locale.value))
const soldOut = computed(() => isSoldOut(props.product))
const as = computed(() => afterSalesLabel(props.product?.after_sales_hours, props.product?.after_sales_months))
</script>

<template>
  <div class="prow">
    <RouterLink :to="`/p/${encodeURIComponent(product.id)}`" class="prow-main" :aria-label="product.title">
      <ProductThumb :src="product.cover" :title="product.title" :size="56" :radius="8" />
      <div class="prow-body">
        <div class="prow-title">{{ product.title }}</div>
        <div class="prow-badges">
          <span :class="product.delivery_mode === 'auto_card' ? 'badge-instant' : 'badge-manual'">{{
            t(`deliveryMode.${product.delivery_mode}`)
          }}</span>
          <span v-if="as" class="badge-as">{{ t(as.key, { n: as.n }) }}</span>
        </div>
        <div class="prow-meta muted">
          <span v-if="product.stock !== null && product.stock !== undefined">{{
            soldOut ? t('product.outOfStock') : t('product.stock', { n: formatNumber(product.stock, locale) })
          }}</span>
          <span>{{ t('home.sold', { n: formatNumber(product.sold, locale) }) }}</span>
        </div>
      </div>
    </RouterLink>
    <div class="prow-side">
      <span class="price prow-price"><small v-if="product.price_from">{{ t('product.priceFromPre') }}</small><small>{{ price.symbol }}</small>{{ price.value }}<small v-if="product.price_from">{{ t('product.priceFromSuf') }}</small></span>
      <button type="button" class="prow-buy" :disabled="soldOut" :aria-label="`${t('product.buy')} — ${product.title}`" @click="emit('buy', product)">
        {{ soldOut ? t('product.soldOutShort') : t('product.buy') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.prow {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-line);
}
.prow-main {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: 10px;
  align-items: center;
  text-decoration: none;
  color: inherit;
  min-height: 56px;
}
.prow-body {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.prow-title {
  font-size: 14px;
  line-height: 19px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.prow-badges {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.badge-as {
  font-size: 11px;
  line-height: 18px;
  padding: 0 6px;
  border-radius: 4px;
  background: #e8f6ee;
  color: #17744a;
  font-weight: 600;
  white-space: nowrap;
}
.prow-meta {
  display: flex;
  gap: 8px;
  font-size: 11px;
}
.prow-side {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.prow-price {
  font-size: 18px;
  line-height: 22px;
}
.prow-price small {
  font-size: 11px;
  margin-right: 1px;
}
.prow-buy {
  min-height: 44px;
  min-width: 60px;
  padding: 0 14px;
  border: 0;
  border-radius: 999px;
  background: var(--c-accent);
  color: var(--c-on-accent);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
.prow-buy:disabled {
  background: #e4e4e4;
  color: var(--c-muted);
  cursor: default;
}
</style>

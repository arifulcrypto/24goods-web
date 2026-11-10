<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ProductThumb } from '@24goods/shared/ui'
import { moneyParts, formatNumber, type ProductSummary } from '@24goods/shared'
import { isSoldOut } from '../lib/stock'

const props = defineProps<{ product: ProductSummary }>()
const { t, locale } = useI18n()
const price = computed(() => moneyParts(props.product.price, props.product.currency, locale.value))
const soldOut = computed(() => isSoldOut(props.product))
</script>

<template>
  <RouterLink :to="`/p/${encodeURIComponent(product.id)}`" class="pcard">
    <div class="pcard-cover">
      <ProductThumb :src="product.cover" :title="product.title" size="100%" :radius="0" />
      <span v-if="soldOut" class="pcard-soldout">{{ t('product.outOfStock') }}</span>
    </div>
    <div class="pcard-body">
      <div class="pcard-title">
        <span :class="product.delivery_mode === 'auto_card' ? 'badge-instant' : 'badge-manual'">{{
          t(`deliveryMode.${product.delivery_mode}`)
        }}</span>
        {{ product.title }}
      </div>
      <div class="pcard-meta">
        <i18n-t v-if="product.price_from" keypath="product.priceFrom" tag="span" class="price"><template #price><small>{{ price.symbol }}</small>{{ price.value }}</template></i18n-t><span v-else class="price"><small>{{ price.symbol }}</small>{{ price.value }}</span>
        <span class="muted pcard-sold">{{ t('home.sold', { n: formatNumber(product.sold, locale) }) }}</span>
      </div>
    </div>
  </RouterLink>
</template>

<style scoped>
.pcard {
  display: flex;
  flex-direction: column;
  background: var(--c-surface);
  border-radius: var(--radius-m);
  overflow: hidden;
  text-decoration: none;
  color: inherit;
  box-shadow: var(--shadow-1);
  min-width: 0;
}
.pcard:active {
  transform: scale(0.99);
}
.pcard-cover {
  position: relative;
  aspect-ratio: 1 / 1;
  background: #f0eee8;
  overflow: hidden;
}
.pcard-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.pcard-ph {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 32px;
  font-weight: 800;
  color: rgba(0, 0, 0, 0.12);
}
.pcard-soldout {
  position: absolute;
  inset: auto 0 0 0;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 12px;
  text-align: center;
  padding: 4px;
}
.pcard-body {
  padding: 8px 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}
.pcard-title {
  font-size: 14px;
  line-height: 20px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
  min-height: 40px;
}
.pcard-title .badge-instant,
.pcard-title .badge-manual {
  margin-right: 4px;
  vertical-align: 1px;
}
.pcard-meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 6px;
  margin-top: auto;
}
.pcard-meta .price {
  font-size: 17px;
}
.pcard-meta small {
  font-size: 12px;
  margin-right: 1px;
}
.pcard-sold {
  font-size: 12px;
  white-space: nowrap;
}
</style>

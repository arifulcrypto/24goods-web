<script setup lang="ts">
import type { ProductSummary } from '@24goods/shared'
import ProductCard from './ProductCard.vue'

defineProps<{ items: ProductSummary[]; skeleton?: number }>()
</script>

<template>
  <div class="pgrid">
    <ProductCard v-for="p in items" :key="p.id" :product="p" />
    <template v-if="skeleton">
      <div v-for="i in skeleton" :key="`s${i}`" class="pskel" aria-hidden="true">
        <div class="skeleton pskel-cover" />
        <div class="skeleton pskel-line" />
        <div class="skeleton pskel-line short" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.pgrid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  padding: 0 12px;
}
@media (min-width: 768px) {
  .pgrid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
    padding: 0 16px;
  }
}
.pskel {
  background: var(--c-surface);
  border-radius: var(--radius-m);
  overflow: hidden;
  padding-bottom: 10px;
}
.pskel-cover {
  aspect-ratio: 1 / 1;
  border-radius: 0;
}
.pskel-line {
  height: 14px;
  margin: 10px 10px 0;
}
.pskel-line.short {
  width: 50%;
}
</style>

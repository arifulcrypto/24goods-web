<script setup lang="ts">
import type { ProductSummary } from '@24goods/shared'
import ProductMini from './ProductMini.vue'

defineProps<{ title: string; items: ProductSummary[]; clearLabel?: string }>()
const emit = defineEmits<{ clear: [] }>()
</script>

<template>
  <section v-if="items.length" class="strip">
    <div class="strip-head">
      <h2>{{ title }}</h2>
      <button v-if="clearLabel" type="button" class="strip-clear" @click="emit('clear')">{{ clearLabel }}</button>
    </div>
    <div class="strip-row">
      <ProductMini v-for="p in items" :key="p.id" :product="p" />
    </div>
  </section>
</template>

<style scoped>
.strip {
  margin: 8px 0 4px;
}
.strip-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
}
h2 {
  font-size: 15px;
  margin: 6px 0;
}
.strip-clear {
  min-height: 44px;
  min-width: 44px;
  border: 0;
  background: none;
  color: var(--c-muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.strip-row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding: 0 12px 4px;
  scrollbar-width: none;
}
.strip-row::-webkit-scrollbar {
  display: none;
}
</style>

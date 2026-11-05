<script setup lang="ts">
import { computed } from 'vue'
import type { Category, ProductSummary } from '@24goods/shared'
import ProductRow from './ProductRow.vue'
import ProductGrid from './ProductGrid.vue'

const props = defineProps<{
  items: ProductSummary[]
  view: 'list' | 'grid'
  categories?: Category[]
  grouped?: boolean
  skeleton?: number
}>()
const emit = defineEmits<{ buy: [p: ProductSummary] }>()

const groups = computed(() => {
  const names = new Map((props.categories ?? []).map((c) => [c.key, c.name]))
  const m = new Map<string, { key: string; name: string; items: ProductSummary[] }>()
  for (const p of props.items) {
    let g = m.get(p.category)
    if (!g) {
      g = { key: p.category, name: names.get(p.category) ?? p.category, items: [] }
      m.set(p.category, g)
    }
    g.items.push(p)
  }
  return [...m.values()]
})
</script>

<template>
  <ProductGrid v-if="view === 'grid'" :items="items" :skeleton="skeleton" />
  <div v-else class="plist">
    <template v-if="grouped && groups.length > 1">
      <section v-for="g in groups" :key="g.key">
        <h3 class="gh">{{ g.name }}</h3>
        <ProductRow v-for="p in g.items" :key="p.id" :product="p" @buy="emit('buy', $event)" />
      </section>
    </template>
    <ProductRow v-for="p in grouped && groups.length > 1 ? [] : items" :key="p.id" :product="p" @buy="emit('buy', $event)" />
    <template v-if="skeleton">
      <div v-for="i in skeleton" :key="`s${i}`" class="rskel" aria-hidden="true">
        <div class="skeleton" style="width: 56px; height: 56px; border-radius: 8px" />
        <div style="flex: 1">
          <div class="skeleton" style="height: 14px" />
          <div class="skeleton" style="height: 12px; width: 50%; margin-top: 8px" />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.plist {
  background: var(--c-surface);
}
.gh {
  position: sticky;
  top: 0;
  z-index: 5;
  margin: 0;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 700;
  background: var(--c-accent-soft);
  color: var(--c-accent-ink);
  border-bottom: 1px solid var(--c-line);
}
.rskel {
  display: flex;
  gap: 10px;
  padding: 12px;
  align-items: center;
}
</style>

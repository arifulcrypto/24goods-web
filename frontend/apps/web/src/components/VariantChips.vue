<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ProductVariant } from '@24goods/shared'

defineProps<{ variants: readonly ProductVariant[]; modelValue: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [id: string] }>()
const { t } = useI18n()

function soldOut(v: ProductVariant): boolean {
  return v.stock !== null && v.stock !== undefined && v.stock <= 0
}
</script>

<template>
  <div class="variants" role="radiogroup" :aria-label="t('product.variantLabel')">
    <button
      v-for="v in variants"
      :key="v.id"
      type="button"
      role="radio"
      class="chip"
      :class="{ on: v.id === modelValue, off: soldOut(v) }"
      :aria-checked="v.id === modelValue"
      :aria-label="soldOut(v) ? t('product.variantSoldOut', { name: v.name }) : v.name"
      @click="emit('update:modelValue', v.id)"
    >
      {{ v.name }}
    </button>
  </div>
</template>

<style scoped>
.variants {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.chip {
  min-height: 44px;
  min-width: 44px;
  padding: 0 14px;
  border: 1px solid var(--c-line);
  border-radius: 22px;
  background: var(--c-surface);
  color: inherit;
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
.chip.on {
  border-color: var(--c-accent-strong);
  color: var(--c-accent-ink);
  background: var(--c-accent-soft);
  font-weight: 600;
}
.chip.off {
  opacity: 0.5;
  text-decoration: line-through;
}
</style>

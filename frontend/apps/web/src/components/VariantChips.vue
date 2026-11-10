<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ProductVariant } from '@24goods/shared'

const props = defineProps<{ variants: readonly ProductVariant[]; modelValue: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [id: string] }>()
const { t } = useI18n()
const group = ref<HTMLElement | null>(null)

function soldOut(v: ProductVariant): boolean {
  return v.stock !== null && v.stock !== undefined && v.stock <= 0
}
function pick(v: ProductVariant) {
  if (!soldOut(v)) emit('update:modelValue', v.id)
}
/** Roving tabindex: the checked chip is the tab stop (the first when none is checked). */
function tab(v: ProductVariant, i: number): number {
  const has = props.variants.some((x) => x.id === props.modelValue)
  return has ? (v.id === props.modelValue ? 0 : -1) : i === 0 ? 0 : -1
}
function onKey(e: KeyboardEvent) {
  const btns = Array.from(group.value?.querySelectorAll<HTMLElement>('[role=radio]') ?? [])
  const i = btns.indexOf(document.activeElement as HTMLElement)
  if (i < 0) return
  const rtl = getComputedStyle(group.value!).direction === 'rtl'
  let n = -1
  if (e.key === 'ArrowRight') n = i + (rtl ? -1 : 1)
  else if (e.key === 'ArrowLeft') n = i + (rtl ? 1 : -1)
  else if (e.key === 'ArrowDown') n = i + 1
  else if (e.key === 'ArrowUp') n = i - 1
  else if (e.key === 'Home') n = 0
  else if (e.key === 'End') n = btns.length - 1
  if (n < 0) return
  e.preventDefault()
  const t2 = btns[(n + btns.length) % btns.length]
  t2?.focus()
  // Arrow keys move the selection like a native radio group (sold-out chips are skipped on click).
  if (t2 && t2.getAttribute('aria-disabled') !== 'true') t2.click()
}
</script>

<template>
  <div ref="group" class="variants" role="radiogroup" :aria-label="t('product.variantLabel')" @keydown="onKey">
    <button
      v-for="(v, i) in variants"
      :key="v.id"
      type="button"
      role="radio"
      class="chip"
      :class="{ on: v.id === modelValue, off: soldOut(v) }"
      :aria-checked="v.id === modelValue"
      :aria-disabled="soldOut(v) ? 'true' : undefined"
      :tabindex="tab(v, i)"
      :aria-label="soldOut(v) ? t('product.variantSoldOut', { name: v.name }) : v.name"
      @click="pick(v)"
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
  color: var(--c-muted);
  background: var(--c-bg);
  cursor: not-allowed;
  text-decoration: line-through;
}
</style>

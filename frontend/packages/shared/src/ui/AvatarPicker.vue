<script setup lang="ts">
/**
 * v11: grid of the 16 generic avatars (radiogroup, 44 px touch targets). Presentational only:
 * the host app calls `PUT /me/avatar/preset` on `pick`. `modelValue` is the stored preset (or null).
 */
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { AVATAR_PRESETS, avatarPresetUrl } from '../avatars'

const props = defineProps<{ modelValue: string | null | undefined; label: string; disabled?: boolean; itemLabel?: (n: number) => string }>()
const emit = defineEmits<{ pick: [preset: string] }>()
const { t } = useI18n()
const group = ref<HTMLElement | null>(null)

/** Roving tabindex: the checked preset is the single tab stop (the first when none is checked). */
function tab(p: string, i: number): number {
  const checked = AVATAR_PRESETS.indexOf(p as (typeof AVATAR_PRESETS)[number]) >= 0 && props.modelValue === p
  if (props.modelValue && AVATAR_PRESETS.includes(props.modelValue as (typeof AVATAR_PRESETS)[number])) return checked ? 0 : -1
  return i === 0 ? 0 : -1
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
  btns[(n + btns.length) % btns.length]?.focus()
}
</script>

<template>
  <div ref="group" class="ap" role="radiogroup" :aria-label="label" @keydown="onKey">
    <button
      v-for="(p, i) in AVATAR_PRESETS"
      :key="p"
      type="button"
      role="radio"
      class="ap-item"
      :class="{ on: modelValue === p }"
      :aria-checked="modelValue === p"
      :aria-label="itemLabel ? itemLabel(i + 1) : t('avatar.preset', { n: i + 1 })"
      :tabindex="tab(p, i)"
      :disabled="disabled"
      @click="emit('pick', p)"
    >
      <img :src="avatarPresetUrl(p)" alt="" width="40" height="40" decoding="async" />
    </button>
  </div>
</template>

<style scoped>
.ap {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(48px, 1fr));
  gap: 8px;
}
.ap-item {
  min-width: 44px;
  min-height: 44px;
  padding: 2px;
  border: 2px solid transparent;
  border-radius: 50%;
  background: none;
  cursor: pointer;
  display: grid;
  place-items: center;
}
.ap-item img {
  width: 40px;
  height: 40px;
  border-radius: 50%;
}
.ap-item.on {
  border-color: var(--c-accent-ink, var(--el-color-primary, #2b2b2b));
}
.ap-item:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>

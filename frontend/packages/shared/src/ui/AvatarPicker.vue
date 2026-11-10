<script setup lang="ts">
/**
 * v11: grid of the 16 generic avatars (radiogroup, 44 px touch targets). Presentational only:
 * the host app calls `PUT /me/avatar/preset` on `pick`. `modelValue` is the stored preset (or null).
 */
import { AVATAR_PRESETS, avatarPresetUrl } from '../avatars'

defineProps<{ modelValue: string | null | undefined; label: string; disabled?: boolean; itemLabel?: (n: number) => string }>()
const emit = defineEmits<{ pick: [preset: string] }>()
</script>

<template>
  <div class="ap" role="radiogroup" :aria-label="label">
    <button
      v-for="(p, i) in AVATAR_PRESETS"
      :key="p"
      type="button"
      role="radio"
      class="ap-item"
      :class="{ on: modelValue === p }"
      :aria-checked="modelValue === p"
      :aria-label="itemLabel ? itemLabel(i + 1) : p"
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

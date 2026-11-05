<script setup lang="ts">
/**
 * Product image with a generated fallback (gradient + initials). Inline SVG, so it needs no
 * storage, no endpoint and no CSP change. Identical in buyer and admin apps.
 */
import { computed, ref, watch } from 'vue'
import { coverColors, coverInitials, hash32 } from './cover'

const props = withDefaults(
  defineProps<{ src?: string | null; title: string; size?: number | string; radius?: number; alt?: string }>(),
  { src: null, size: 56, radius: 8, alt: '' },
)

const failed = ref(false)
watch(
  () => props.src,
  () => (failed.value = false),
)
const showImg = computed(() => !!props.src && !failed.value)
const colors = computed(() => coverColors(props.title))
const initials = computed(() => coverInitials(props.title))
const gid = computed(() => `pt${hash32(props.title).toString(36)}`)
const dim = computed(() => (typeof props.size === 'number' ? `${props.size}px` : props.size))
</script>

<template>
  <span class="pt" :style="{ width: dim, height: dim, borderRadius: `${radius}px` }">
    <img v-if="showImg" :src="src!" :alt="alt" loading="lazy" decoding="async" @error="failed = true" />
    <svg v-else viewBox="0 0 100 100" role="img" :aria-label="alt || title" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient :id="gid" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" :stop-color="colors[0]" />
          <stop offset="1" :stop-color="colors[1]" />
        </linearGradient>
      </defs>
      <rect width="100" height="100" :fill="`url(#${gid})`" />
      <circle cx="86" cy="14" r="26" fill="#fff" opacity=".12" />
      <circle cx="10" cy="96" r="20" fill="#fff" opacity=".08" />
      <text
        x="50"
        y="53"
        text-anchor="middle"
        dominant-baseline="middle"
        fill="#fff"
        font-weight="700"
        :font-size="initials.length > 1 ? 38 : 46"
        font-family="system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif"
      >{{ initials }}</text>
    </svg>
  </span>
</template>

<style scoped>
.pt {
  display: inline-block;
  flex: none;
  overflow: hidden;
  background: #eef0f3;
  vertical-align: middle;
}
.pt img,
.pt svg {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>

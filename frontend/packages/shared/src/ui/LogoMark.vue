<script setup lang="ts">
/** Brand monogram (review-v3 §1). Also the favicon source (each app public/favicon.svg). */
withDefaults(defineProps<{ size?: number; title?: string }>(), { size: 28, title: '' })
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 64 64" :role="title ? 'img' : 'presentation'" :aria-label="title || undefined" :aria-hidden="title ? undefined : 'true'">
    <defs>
      <linearGradient id="kesiv-g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#ff6a2b" />
        <stop offset="1" stop-color="#d93f00" />
      </linearGradient>
    </defs>
    <rect width="64" height="64" rx="15" fill="url(#kesiv-g)" />
    <path d="M20 14h8v15l13-15h10L37 30l15 20H42L31.5 35.5 28 39.5V50h-8z" fill="#fff" />
  </svg>
</template>

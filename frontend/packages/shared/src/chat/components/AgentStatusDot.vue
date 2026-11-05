<script setup lang="ts">
import type { AgentStatusValue } from '../../types'
defineProps<{ status: AgentStatusValue | null | undefined }>()
</script>

<template>
  <span class="gc-dot" :class="`dot-${status ?? 'offline'}`" aria-hidden="true" />
</template>

<style scoped>
.gc-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #b0b0b0;
  flex: none;
}
.dot-online {
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18);
}
.dot-away {
  background: #f59e0b;
}
.dot-on_hold {
  background: #ef4444;
}
</style>

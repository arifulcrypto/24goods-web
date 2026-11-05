<script setup lang="ts">
import { useI18n } from 'vue-i18n'
defineProps<{ solving: boolean; progress: number }>()
const { t } = useI18n()
</script>

<template>
  <div v-if="solving" class="pow" role="status" aria-live="polite">
    <span>{{ t('pow.running') }}</span>
    <van-progress :percentage="Math.round(progress * 100)" stroke-width="6" :show-pivot="false" color="var(--c-price)" />
  </div>
</template>

<style scoped>
.pow {
  font-size: 12px;
  color: var(--c-text-2);
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 8px 0;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { minorToYuan, permilleToPct, pctToPermille, yuanToMinor } from '../lib/money'

/** Markup editor: kind + value. Percent is shown as % (stored permille); fixed shown in yuan (stored minor). */
const kind = defineModel<'percent' | 'fixed'>('kind', { required: true })
const value = defineModel<number>('value', { required: true })
const { t } = useI18n()
const shown = computed({
  get: () => (kind.value === 'percent' ? permilleToPct(value.value) : Number(minorToYuan(value.value) || 0)),
  set: (v: number | undefined) => {
    const n = Number(v ?? 0)
    value.value = kind.value === 'percent' ? pctToPermille(n) : (yuanToMinor(n.toFixed(2)) ?? 0)
  },
})
function onKind(k: 'percent' | 'fixed') {
  kind.value = k
  value.value = 0
}
</script>

<template>
  <div class="mk">
    <el-radio-group :model-value="kind" @update:model-value="onKind($event as 'percent' | 'fixed')">
      <el-radio-button value="percent">{{ t('v5.markup.percent') }}</el-radio-button>
      <el-radio-button value="fixed">{{ t('v5.markup.fixed') }}</el-radio-button>
    </el-radio-group>
    <el-input-number v-model="shown" :min="0" :max="kind === 'percent' ? 1000 : 100000" :step="kind === 'percent' ? 1 : 1" :precision="kind === 'percent' ? 1 : 2" />
    <span class="muted">{{ kind === 'percent' ? '%' : '¥' }}</span>
  </div>
</template>

<style scoped>
.mk {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>

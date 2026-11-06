<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatMoney } from '@24goods/shared'
import { apiV5 } from '../../api'
import type { AdminSiteV5 } from '../../api-v5'
import { suggestPrice } from '../../lib/money'
import { useSave } from '../../composables/useSave'
import MarkupInput from '../MarkupInput.vue'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t, locale } = useI18n()
const { saving, run } = useSave()
const f = reactive({ kind: 'percent' as 'percent' | 'fixed', value: 200, round: 10, retention: 365 })
watch(
  () => props.site,
  (s) => {
    f.kind = s.import?.markup_kind ?? 'percent'
    f.value = s.import?.markup_value ?? 200
    f.round = s.import?.round_to_minor ?? 10
    f.retention = s.audit?.retention_days ?? 365
  },
  { immediate: true },
)
const example = computed(() => formatMoney((suggestPrice(10000, f.kind, f.value, f.round) / 100).toFixed(2), 'CNY', locale.value))
async function save() {
  const r = await run(() =>
    apiV5.site.put({
      import: { markup_kind: f.kind, markup_value: f.value, round_to_minor: f.round },
      audit: { retention_days: f.retention },
    }),
  )
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.import.title') }}</h2>
      <p class="muted small">{{ t('v5.import.note') }}</p>
      <el-form-item :label="t('v5.import.markup')"><MarkupInput v-model:kind="f.kind" v-model:value="f.value" /></el-form-item>
      <el-form-item :label="t('v5.import.round')">
        <el-select v-model="f.round" style="width: 220px">
          <el-option :value="1" :label="t('v5.import.round1')" />
          <el-option :value="10" :label="t('v5.import.round10')" />
          <el-option :value="100" :label="t('v5.import.round100')" />
        </el-select>
      </el-form-item>
      <p class="muted small">{{ t('v5.import.example', { price: example }) }}</p>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.import.auditTitle') }}</h2>
      <el-form-item :label="t('v5.import.retention')"><el-input-number v-model="f.retention" :min="30" :max="3650" /><span class="unit muted">{{ t('v5.days') }}</span></el-form-item>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>

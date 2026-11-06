<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import type { AdminSiteV5 } from '../../api-v5'
import { useSave } from '../../composables/useSave'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const f = reactive({ locale: 'en' as 'en' | 'zh-CN', max_pending_orders: 5, order_timeout_minutes: 30 })
watch(
  () => props.site.defaults,
  (d) => {
    f.locale = d?.locale ?? 'en'
    f.max_pending_orders = d?.max_pending_orders ?? 5
    f.order_timeout_minutes = d?.order_timeout_minutes ?? 30
  },
  { immediate: true },
)
async function save() {
  const r = await run(() => apiV5.site.put({ defaults: { ...f } }))
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.defaults.title') }}</h2>
      <el-form-item :label="t('v5.defaults.locale')">
        <el-radio-group v-model="f.locale">
          <el-radio-button value="en">{{ t('lang.en') }}</el-radio-button>
          <el-radio-button value="zh-CN">{{ t('lang.zh') }}</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item :label="t('v5.defaults.maxPending')">
        <el-input-number v-model="f.max_pending_orders" :min="1" :max="50" />
        <div class="muted small full">{{ t('v5.defaults.maxPendingHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('v5.defaults.timeout')">
        <el-input-number v-model="f.order_timeout_minutes" :min="5" :max="1440" /><span class="unit muted">{{ t('v5.minutes') }}</span>
      </el-form-item>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>
<style scoped>
.full {
  width: 100%;
}
</style>

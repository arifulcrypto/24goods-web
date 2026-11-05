<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV3 } from '../api'
import type { AdminSiteSettings } from '../api-v3'
import { toastError, toastOk } from '../ui'

const { t } = useI18n()
const loading = ref(false)
const saving = ref(false)
const version = ref<number | null>(null)
const tab = ref<'en' | 'zh-CN'>('en')
const errors = ref<Record<string, string>>({})
const f = reactive({
  enabled: false,
  title_en: '',
  title_zh: '',
  body_en: '',
  body_zh: '',
  after_sales_default_hours: 672,
  manual: 72,
  auto_card: 24,
  support_hours: '',
})

function apply(s: AdminSiteSettings) {
  const a = s.announcement
  version.value = a?.version ?? null
  f.enabled = a?.enabled ?? false
  f.title_en = a?.title_i18n.en ?? ''
  f.title_zh = a?.title_i18n['zh-CN'] ?? ''
  f.body_en = a?.body_i18n.en ?? ''
  f.body_zh = a?.body_i18n['zh-CN'] ?? ''
  f.after_sales_default_hours = s.after_sales_default_hours
  f.manual = s.auto_confirm_hours.manual
  f.auto_card = s.auto_confirm_hours.auto_card
  f.support_hours = s.support_hours ?? ''
}
onMounted(async () => {
  loading.value = true
  try {
    apply(await apiV3.site.get())
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
})
const okHours = (n: number, max: number) => Number.isInteger(n) && n >= 0 && n <= max

async function save() {
  if (saving.value) return
  const e: Record<string, string> = {}
  if (f.enabled && !f.title_en.trim()) e.title = t('articles.v.titleRequired')
  if (!okHours(f.after_sales_default_hours, 8760)) e.as = t('delivery.hoursInvalid')
  if (!okHours(f.manual, 720) || !okHours(f.auto_card, 720)) e.ac = t('site.v.autoConfirm')
  errors.value = e
  if (Object.keys(e).length) return
  saving.value = true
  try {
    apply(
      await apiV3.site.put({
        announcement: {
          enabled: f.enabled,
          title_i18n: { en: f.title_en.trim(), 'zh-CN': f.title_zh.trim() },
          body_i18n: { en: f.body_en, 'zh-CN': f.body_zh },
        },
        after_sales_default_hours: f.after_sales_default_hours,
        auto_confirm_hours: { manual: f.manual, auto_card: f.auto_card },
        support_hours: f.support_hours.trim() || null,
      }),
    )
    toastOk(t('site.saved'))
  } catch (err) {
    toastError(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-loading="loading" class="page">
    <el-form label-position="top" class="wrap" @submit.prevent="save">
      <section class="panel block">
        <h2 class="h2">{{ t('site.announcement') }}</h2>
        <p class="muted small">{{ t('site.announcementNote', { v: version ?? '—' }) }}</p>
        <el-form-item :label="t('site.enabled')"><el-switch v-model="f.enabled" /></el-form-item>
        <el-tabs v-model="tab">
          <el-tab-pane :label="t('products.form.tabEn')" name="en">
            <el-form-item :label="t('products.form.title')" :error="errors.title"><el-input v-model="f.title_en" maxlength="120" show-word-limit /></el-form-item>
            <el-form-item :label="t('articles.body')"><el-input v-model="f.body_en" type="textarea" :rows="6" maxlength="5000" show-word-limit /></el-form-item>
          </el-tab-pane>
          <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
            <el-form-item :label="t('products.form.title')"><el-input v-model="f.title_zh" maxlength="120" show-word-limit /></el-form-item>
            <el-form-item :label="t('articles.body')"><el-input v-model="f.body_zh" type="textarea" :rows="6" maxlength="5000" show-word-limit /></el-form-item>
          </el-tab-pane>
        </el-tabs>
      </section>
      <section class="panel block">
        <h2 class="h2">{{ t('site.defaults') }}</h2>
        <el-form-item :label="t('site.afterSalesHours')" :error="errors.as">
          <el-input-number v-model="f.after_sales_default_hours" :min="0" :max="8760" />
          <span class="unit muted">{{ t('site.hours') }} ({{ Math.round((f.after_sales_default_hours / 24) * 10) / 10 }} {{ t('site.days') }})</span>
        </el-form-item>
        <el-form-item :label="t('site.autoConfirmManual')" :error="errors.ac">
          <el-input-number v-model="f.manual" :min="0" :max="720" /><span class="unit muted">{{ t('site.hours') }}</span>
        </el-form-item>
        <el-form-item :label="t('site.autoConfirmAuto')">
          <el-input-number v-model="f.auto_card" :min="0" :max="720" /><span class="unit muted">{{ t('site.hours') }}</span>
        </el-form-item>
        <p class="muted small">{{ t('site.autoConfirmNote') }}</p>
        <el-form-item :label="t('site.supportHours')">
          <el-input v-model="f.support_hours" maxlength="120" :placeholder="t('site.supportPlaceholder')" />
        </el-form-item>
      </section>
      <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
    </el-form>
  </div>
</template>

<style scoped>
.wrap {
  max-width: 760px;
}
.block {
  margin-bottom: 16px;
}
.h2 {
  font-size: 16px;
  margin: 0 0 8px;
}
.small {
  font-size: 12px;
}
.unit {
  margin-left: 10px;
  font-size: 13px;
}
</style>

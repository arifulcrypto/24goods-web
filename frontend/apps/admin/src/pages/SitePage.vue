<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV4 } from '../api'
import type { AdminSiteSettings } from '../api-v3'
import {
  DEFAULT_SURCHARGE_PERMILLE,
  MAX_QUICK_LEN,
  MAX_QUICK_QUESTIONS,
  MAX_SURCHARGE_PERMILLE,
  RATE_LIMIT_MESSAGE,
  RATE_LIMIT_MESSAGE_ZH,
  type AdminSiteV4,
  type ChannelCfg,
} from '../api-v4'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import { cleanQuickQuestions, percentToPermille, permilleToPercent } from '../lib/buyerInput'
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
  support_en: '',
  support_zh: '',
  alipay_enabled: true,
  alipay_pct: permilleToPercent(DEFAULT_SURCHARGE_PERMILLE),
  rl_enabled: false,
  rl_limit: 120,
  rl_window: 60,
  welcome_en: '',
  welcome_zh: '',
  qq_en: '',
  qq_zh: '',
})
const otherChannels = ref<Record<string, ChannelCfg>>({})
const defaults = ref<AdminSiteSettings['defaults'] | null>(null)

function apply(s: AdminSiteV4) {
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
  f.support_en = s.support_hours?.en ?? ''
  f.support_zh = s.support_hours?.['zh-CN'] ?? ''
  defaults.value = s.defaults ?? null
  const { zpay_alipay, ...others } = s.payments?.channels ?? {}
  otherChannels.value = others
  f.alipay_enabled = zpay_alipay?.enabled ?? true
  f.alipay_pct = permilleToPercent(zpay_alipay?.surcharge_permille ?? DEFAULT_SURCHARGE_PERMILLE)
  f.rl_enabled = s.rate_limit?.enabled ?? false
  f.rl_limit = s.rate_limit?.limit ?? 120
  f.rl_window = s.rate_limit?.window_s ?? 60
  f.welcome_en = s.support?.welcome_i18n?.en ?? ''
  f.welcome_zh = s.support?.welcome_i18n?.['zh-CN'] ?? ''
  f.qq_en = (s.support?.quick_questions_i18n?.en ?? []).join('\n')
  f.qq_zh = (s.support?.quick_questions_i18n?.['zh-CN'] ?? []).join('\n')
}
const qqList = (v: string) => cleanQuickQuestions(v.split(/\r?\n/))
const qqOk = (v: string) => {
  const l = qqList(v)
  return l.length <= MAX_QUICK_QUESTIONS && l.every((x) => x.length <= MAX_QUICK_LEN)
}
const feeExample = computed(() => {
  return (percentToPermille(f.alipay_pct) / 100).toFixed(2)
})
onMounted(async () => {
  loading.value = true
  try {
    apply(await apiV4.site.get())
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
  if (!okHours(f.manual, 720) || !okHours(f.auto_card, 720) || f.manual < 1 || f.auto_card < 1) e.ac = t('site.v.autoConfirm')
  const pm = percentToPermille(f.alipay_pct)
  if (!Number.isInteger(pm) || pm < 0 || pm > MAX_SURCHARGE_PERMILLE) e.fee = t('site.v.fee')
  if (!Number.isInteger(f.rl_limit) || f.rl_limit < 10 || f.rl_limit > 10000) e.rl = t('site.v.rlLimit')
  else if (!Number.isInteger(f.rl_window) || f.rl_window < 1 || f.rl_window > 3600) e.rl = t('site.v.rlWindow')
  if (!qqOk(f.qq_en) || !qqOk(f.qq_zh)) e.qq = t('site.v.qq', { n: MAX_QUICK_QUESTIONS, len: MAX_QUICK_LEN })
  if (f.welcome_en.length > 500 || f.welcome_zh.length > 500) e.welcome = t('site.v.welcome')
  errors.value = e
  if (Object.keys(e).length) return
  saving.value = true
  try {
    apply(
      await apiV4.site.put({
        announcement: {
          enabled: f.enabled,
          title_i18n: { en: f.title_en.trim(), 'zh-CN': f.title_zh.trim() },
          body_i18n: { en: f.body_en, 'zh-CN': f.body_zh },
        },
        after_sales_default_hours: f.after_sales_default_hours,
        auto_confirm_hours: { manual: f.manual, auto_card: f.auto_card },
        support_hours: { en: f.support_en.trim(), 'zh-CN': f.support_zh.trim() },
        payments: { channels: { ...otherChannels.value, zpay_alipay: { enabled: f.alipay_enabled, surcharge_permille: pm } } },
        support: {
          welcome_i18n: { en: f.welcome_en.trim(), 'zh-CN': f.welcome_zh.trim() },
          quick_questions_i18n: { en: qqList(f.qq_en), 'zh-CN': qqList(f.qq_zh) },
        },
        rate_limit: { enabled: f.rl_enabled, limit: f.rl_limit, window_s: f.rl_window },
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
        <div class="toggle" :class="{ on: f.enabled }">
          <el-switch v-model="f.enabled" size="large" :active-text="t('site.enabledOn')" :inactive-text="t('site.enabledOff')" />
          <span class="muted small">{{ f.enabled ? t('site.enabledNoteOn') : t('site.enabledNoteOff') }}</span>
        </div>
        <el-tabs v-model="tab">
          <el-tab-pane :label="t('products.form.tabEn')" name="en">
            <el-form-item :label="t('products.form.title')" :error="errors.title"><el-input v-model="f.title_en" maxlength="120" show-word-limit /></el-form-item>
            <el-form-item :label="t('articles.body')"><MarkdownEditor v-model="f.body_en" :rows="6" :maxlength="5000" /></el-form-item>
          </el-tab-pane>
          <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
            <el-form-item :label="t('products.form.title')"><el-input v-model="f.title_zh" maxlength="120" show-word-limit /></el-form-item>
            <el-form-item :label="t('articles.body')"><MarkdownEditor v-model="f.body_zh" :rows="6" :maxlength="5000" /></el-form-item>
          </el-tab-pane>
        </el-tabs>
      </section>
      <section class="panel block">
        <h2 class="h2">{{ t('site.channels') }}</h2>
        <p class="muted small">{{ t('site.feeNote') }}</p>
        <el-form-item :label="t('site.alipayEnabled')"><el-switch v-model="f.alipay_enabled" /></el-form-item>
        <el-form-item :label="t('site.alipayFee')" :error="errors.fee">
          <el-input-number v-model="f.alipay_pct" :min="0" :max="MAX_SURCHARGE_PERMILLE / 10" :step="0.1" :precision="1" />
          <span class="unit muted">% ({{ percentToPermille(f.alipay_pct) }}‰)</span>
        </el-form-item>
        <p class="muted small">{{ t('site.feeExample', { fee: feeExample }) }}</p>
      </section>
      <section class="panel block">
        <h2 class="h2">{{ t('site.rateLimit') }}</h2>
        <p class="muted small">{{ t('site.rateLimitNote') }}</p>
        <el-form-item :label="t('site.rlEnabled')"><el-switch v-model="f.rl_enabled" /></el-form-item>
        <el-form-item :label="t('site.rlLimit')" :error="errors.rl">
          <el-input-number v-model="f.rl_limit" :min="10" :max="10000" />
          <span class="unit muted">{{ t('site.rlPer') }}</span>
          <el-input-number v-model="f.rl_window" :min="1" :max="3600" />
          <span class="unit muted">{{ t('site.seconds') }}</span>
        </el-form-item>
        <el-alert type="info" :closable="false" :title="t('site.rlMessage')" :description="`${RATE_LIMIT_MESSAGE} / ${RATE_LIMIT_MESSAGE_ZH}`" />
      </section>
      <section class="panel block">
        <h2 class="h2">{{ t('site.support') }}</h2>
        <p class="muted small">{{ t('site.supportNote') }}</p>
        <el-form-item :label="`${t('site.welcome')} (EN)`" :error="errors.welcome">
          <el-input v-model="f.welcome_en" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item :label="`${t('site.welcome')} (中文)`">
          <el-input v-model="f.welcome_zh" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item :label="`${t('site.quick')} (EN)`" :error="errors.qq">
          <el-input v-model="f.qq_en" type="textarea" :rows="4" />
          <div class="muted small">{{ t('site.quickCount', { n: qqList(f.qq_en).length, max: MAX_QUICK_QUESTIONS, len: MAX_QUICK_LEN }) }}</div>
        </el-form-item>
        <el-form-item :label="`${t('site.quick')} (中文)`">
          <el-input v-model="f.qq_zh" type="textarea" :rows="4" />
          <div class="muted small">{{ t('site.quickCount', { n: qqList(f.qq_zh).length, max: MAX_QUICK_QUESTIONS, len: MAX_QUICK_LEN }) }}</div>
        </el-form-item>
      </section>
      <section class="panel block">
        <h2 class="h2">{{ t('site.defaults') }}</h2>
        <el-form-item :label="t('site.afterSalesHours')" :error="errors.as">
          <el-input-number v-model="f.after_sales_default_hours" :min="0" :max="8760" />
          <span class="unit muted">{{ t('site.hours') }} ({{ Math.round((f.after_sales_default_hours / 24) * 10) / 10 }} {{ t('site.days') }})</span>
        </el-form-item>
        <el-form-item :label="t('site.autoConfirmManual')" :error="errors.ac">
          <el-input-number v-model="f.manual" :min="1" :max="720" /><span class="unit muted">{{ t('site.hours') }}</span>
        </el-form-item>
        <el-form-item :label="t('site.autoConfirmAuto')">
          <el-input-number v-model="f.auto_card" :min="1" :max="720" /><span class="unit muted">{{ t('site.hours') }}</span>
        </el-form-item>
        <p class="muted small">{{ t('site.autoConfirmNote') }}</p>
        <el-form-item :label="`${t('site.supportHours')} (EN)`">
          <el-input v-model="f.support_en" maxlength="200" :placeholder="t('site.supportPlaceholder')" />
        </el-form-item>
        <el-form-item :label="`${t('site.supportHours')} (中文)`">
          <el-input v-model="f.support_zh" maxlength="200" :placeholder="t('site.supportPlaceholder')" />
        </el-form-item>
        <p v-if="defaults" class="muted small">
          {{ t('site.builtinDefaults', { as: defaults.after_sales_default_hours, m: defaults.auto_confirm_hours.manual, a: defaults.auto_confirm_hours.auto_card }) }}
        </p>
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
  margin: 0 10px;
  font-size: 13px;
}
.toggle {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 12px 14px;
  margin-bottom: 12px;
  border-radius: 8px;
  border: 2px solid var(--a-line);
}
.toggle.on {
  border-color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}
</style>

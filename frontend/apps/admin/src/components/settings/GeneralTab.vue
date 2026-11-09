<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import type { AdminSiteV5 } from '../../api-v5'
import { MAX_QUICK_LEN, MAX_QUICK_QUESTIONS } from '../../api-v4'
import { cleanQuickQuestions } from '../../lib/buyerInput'
import { useSave } from '../../composables/useSave'
import MarkdownEditor from '../MarkdownEditor.vue'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const lang = ref<'en' | 'zh-CN'>('en')
const errors = ref<Record<string, string>>({})
const f = reactive({
  name_en: '', name_zh: '', sub_en: '', sub_zh: '', logo: '', contact_en: '', contact_zh: '', footer_en: '', footer_zh: '',
  ann_on: false, ann_display: 'modal' as 'modal' | 'banner' | 'both', ann_ack: false, ann_title_en: '', ann_title_zh: '', ann_body_en: '', ann_body_zh: '',
  support_en: '', support_zh: '', welcome_en: '', welcome_zh: '', qq_en: '', qq_zh: '',
})
watch(
  () => props.site,
  (s) => {
    const g = s.general
    f.name_en = g?.site_name_i18n?.en ?? ''
    f.name_zh = g?.site_name_i18n?.['zh-CN'] ?? ''
    f.sub_en = g?.subtitle_i18n?.en ?? ''
    f.sub_zh = g?.subtitle_i18n?.['zh-CN'] ?? ''
    f.logo = g?.logo_url ?? ''
    f.contact_en = g?.contact_i18n?.en ?? ''
    f.contact_zh = g?.contact_i18n?.['zh-CN'] ?? ''
    f.footer_en = g?.footer_i18n?.en ?? ''
    f.footer_zh = g?.footer_i18n?.['zh-CN'] ?? ''
    const a = s.announcement
    f.ann_on = a?.enabled ?? false
    f.ann_display = a?.display ?? 'modal'
    f.ann_ack = a?.require_ack ?? false
    f.ann_title_en = a?.title_i18n.en ?? ''
    f.ann_title_zh = a?.title_i18n['zh-CN'] ?? ''
    f.ann_body_en = a?.body_i18n.en ?? ''
    f.ann_body_zh = a?.body_i18n['zh-CN'] ?? ''
    f.support_en = s.support_hours?.en ?? ''
    f.support_zh = s.support_hours?.['zh-CN'] ?? ''
    f.welcome_en = s.support?.welcome_i18n?.en ?? ''
    f.welcome_zh = s.support?.welcome_i18n?.['zh-CN'] ?? ''
    f.qq_en = (s.support?.quick_questions_i18n?.en ?? []).join('\n')
    f.qq_zh = (s.support?.quick_questions_i18n?.['zh-CN'] ?? []).join('\n')
  },
  { immediate: true },
)
const qqList = (v: string) => cleanQuickQuestions(v.split(/\r?\n/))
const qqOk = (v: string) => {
  const l = qqList(v)
  return l.length <= MAX_QUICK_QUESTIONS && l.every((x) => x.length <= MAX_QUICK_LEN)
}
const trim2 = (en: string, zh: string) => ({ en: en.trim(), 'zh-CN': zh.trim() })

async function save() {
  const e: Record<string, string> = {}
  if (f.ann_on && !f.ann_title_en.trim()) e.title = t('articles.v.titleRequired')
  if (!qqOk(f.qq_en) || !qqOk(f.qq_zh)) e.qq = t('site.v.qq', { n: MAX_QUICK_QUESTIONS, len: MAX_QUICK_LEN })
  if (f.welcome_en.length > 500 || f.welcome_zh.length > 500) e.welcome = t('site.v.welcome')
  if (f.logo.trim() && !/^(https:\/\/|\/)/.test(f.logo.trim())) e.logo = t('v5.general.logoInvalid')
  errors.value = e
  if (Object.keys(e).length) return
  const r = await run(() =>
    apiV5.site.put({
      general: {
        site_name_i18n: trim2(f.name_en, f.name_zh),
        subtitle_i18n: trim2(f.sub_en, f.sub_zh),
        logo_url: f.logo.trim(),
        contact_i18n: trim2(f.contact_en, f.contact_zh),
        footer_i18n: trim2(f.footer_en, f.footer_zh),
      },
      announcement: {
        enabled: f.ann_on,
        display: f.ann_display,
        require_ack: f.ann_ack,
        title_i18n: trim2(f.ann_title_en, f.ann_title_zh),
        body_i18n: { en: f.ann_body_en, 'zh-CN': f.ann_body_zh },
      },
      support_hours: trim2(f.support_en, f.support_zh),
      support: {
        welcome_i18n: trim2(f.welcome_en, f.welcome_zh),
        quick_questions_i18n: { en: qqList(f.qq_en), 'zh-CN': qqList(f.qq_zh) },
      },
    }),
  )
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.general.brand') }}</h2>
      <p class="muted small">{{ t('v5.general.brandNote') }}</p>
      <div class="grid">
        <el-form-item :label="`${t('v5.general.siteName')} (EN)`"><el-input v-model="f.name_en" maxlength="60" placeholder="Kesiv" /></el-form-item>
        <el-form-item :label="`${t('v5.general.siteName')} (中文)`"><el-input v-model="f.name_zh" maxlength="60" placeholder="客思维" /></el-form-item>
        <el-form-item :label="`${t('v5.general.subtitle')} (EN)`"><el-input v-model="f.sub_en" maxlength="120" /></el-form-item>
        <el-form-item :label="`${t('v5.general.subtitle')} (中文)`"><el-input v-model="f.sub_zh" maxlength="120" /></el-form-item>
        <el-form-item :label="`${t('v5.general.contact')} (EN)`"><el-input v-model="f.contact_en" maxlength="200" /></el-form-item>
        <el-form-item :label="`${t('v5.general.contact')} (中文)`"><el-input v-model="f.contact_zh" maxlength="200" /></el-form-item>
        <el-form-item :label="`${t('v5.general.footer')} (EN)`"><el-input v-model="f.footer_en" maxlength="300" /></el-form-item>
        <el-form-item :label="`${t('v5.general.footer')} (中文)`"><el-input v-model="f.footer_zh" maxlength="300" /></el-form-item>
      </div>
      <el-form-item :label="t('v5.general.logo')" :error="errors.logo">
        <el-input v-model="f.logo" maxlength="500" placeholder="/api/v1/files/…" inputmode="url" />
        <div class="muted small">{{ t('v5.general.logoHint') }}</div>
      </el-form-item>
    </section>

    <section class="panel block">
      <h2 class="h2">{{ t('site.announcement') }}</h2>
      <div class="toggle" :class="{ on: f.ann_on }">
        <el-switch v-model="f.ann_on" size="large" :active-text="t('site.enabledOn')" :inactive-text="t('site.enabledOff')" />
        <span class="muted small">{{ f.ann_on ? t('site.enabledNoteOn') : t('site.enabledNoteOff') }}</span>
      </div>
      <div class="grid">
        <el-form-item :label="t('site.display')">
          <el-radio-group v-model="f.ann_display">
            <el-radio-button value="modal">{{ t('site.displayModal') }}</el-radio-button>
            <el-radio-button value="banner">{{ t('site.displayBanner') }}</el-radio-button>
            <el-radio-button value="both">{{ t('site.displayBoth') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('site.requireAck')">
          <el-switch v-model="f.ann_ack" />
          <div class="muted small">{{ t('site.requireAckNote') }}</div>
        </el-form-item>
      </div>
      <el-tabs v-model="lang">
        <el-tab-pane :label="t('products.form.tabEn')" name="en">
          <el-form-item :label="t('products.form.title')" :error="errors.title"><el-input v-model="f.ann_title_en" maxlength="120" show-word-limit /></el-form-item>
          <el-form-item :label="t('articles.body')"><MarkdownEditor v-model="f.ann_body_en" :rows="6" :maxlength="5000" /></el-form-item>
        </el-tab-pane>
        <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
          <el-form-item :label="t('products.form.title')"><el-input v-model="f.ann_title_zh" maxlength="120" show-word-limit /></el-form-item>
          <el-form-item :label="t('articles.body')"><MarkdownEditor v-model="f.ann_body_zh" :rows="6" :maxlength="5000" /></el-form-item>
        </el-tab-pane>
      </el-tabs>
    </section>

    <section class="panel block">
      <h2 class="h2">{{ t('site.support') }}</h2>
      <p class="muted small">{{ t('site.supportNote') }}</p>
      <el-form-item :label="`${t('site.supportHours')} (EN)`"><el-input v-model="f.support_en" maxlength="200" :placeholder="t('site.supportPlaceholder')" /></el-form-item>
      <el-form-item :label="`${t('site.supportHours')} (中文)`"><el-input v-model="f.support_zh" maxlength="200" :placeholder="t('site.supportPlaceholder')" /></el-form-item>
      <el-form-item :label="`${t('site.welcome')} (EN)`" :error="errors.welcome"><el-input v-model="f.welcome_en" type="textarea" :rows="2" maxlength="500" show-word-limit /></el-form-item>
      <el-form-item :label="`${t('site.welcome')} (中文)`"><el-input v-model="f.welcome_zh" type="textarea" :rows="2" maxlength="500" show-word-limit /></el-form-item>
      <el-form-item :label="`${t('site.quick')} (EN)`" :error="errors.qq">
        <el-input v-model="f.qq_en" type="textarea" :rows="4" />
        <div class="muted small">{{ t('site.quickCount', { n: qqList(f.qq_en).length, max: MAX_QUICK_QUESTIONS, len: MAX_QUICK_LEN }) }}</div>
      </el-form-item>
      <el-form-item :label="`${t('site.quick')} (中文)`">
        <el-input v-model="f.qq_zh" type="textarea" :rows="4" />
        <div class="muted small">{{ t('site.quickCount', { n: qqList(f.qq_zh).length, max: MAX_QUICK_QUESTIONS, len: MAX_QUICK_LEN }) }}</div>
      </el-form-item>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>

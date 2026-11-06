<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import { FEATURE_KEYS, type AdminSiteV5, type FeaturesCfg } from '../../api-v5'
import { RATE_LIMIT_MESSAGE, RATE_LIMIT_MESSAGE_ZH } from '../../api-v4'
import { useSave } from '../../composables/useSave'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const errors = ref<Record<string, string>>({})
const feat = reactive<FeaturesCfg>({
  registration: true, guest_checkout: true, cart: true, promo_codes: true, resellers: false, voice_messages: true, lookup: true, google_login: false,
})
const rl = reactive({ enabled: false, limit: 120, window_s: 60 })
watch(
  () => props.site,
  (s) => {
    for (const k of FEATURE_KEYS) feat[k] = s.features?.[k] ?? feat[k]
    rl.enabled = s.rate_limit?.enabled ?? false
    rl.limit = s.rate_limit?.limit ?? 120
    rl.window_s = s.rate_limit?.window_s ?? 60
  },
  { immediate: true },
)
async function save() {
  const e: Record<string, string> = {}
  if (!Number.isInteger(rl.limit) || rl.limit < 10 || rl.limit > 10000) e.rl = t('site.v.rlLimit')
  else if (!Number.isInteger(rl.window_s) || rl.window_s < 1 || rl.window_s > 3600) e.rl = t('site.v.rlWindow')
  errors.value = e
  if (Object.keys(e).length) return
  const r = await run(() => apiV5.site.put({ features: { ...feat }, rate_limit: { enabled: rl.enabled, limit: rl.limit, window_s: rl.window_s } }))
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.features.title') }}</h2>
      <p class="muted small">{{ t('v5.features.note') }}</p>
      <div v-for="k in FEATURE_KEYS" :key="k" class="opt">
        <div>
          <div>{{ t(`v5.features.k.${k}`) }}</div>
          <div class="muted small">{{ t(`v5.features.h.${k}`) }}</div>
        </div>
        <el-switch v-model="feat[k]" :aria-label="t(`v5.features.k.${k}`)" />
      </div>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('site.rateLimit') }}</h2>
      <p class="muted small">{{ t('site.rateLimitNote') }}</p>
      <el-form-item :label="t('site.rlEnabled')"><el-switch v-model="rl.enabled" /></el-form-item>
      <el-form-item :label="t('site.rlLimit')" :error="errors.rl">
        <el-input-number v-model="rl.limit" :min="10" :max="10000" />
        <span class="unit muted">{{ t('site.rlPer') }}</span>
        <el-input-number v-model="rl.window_s" :min="1" :max="3600" />
        <span class="unit muted">{{ t('site.seconds') }}</span>
      </el-form-item>
      <el-alert type="info" :closable="false" :title="t('site.rlMessage')" :description="`${RATE_LIMIT_MESSAGE} / ${RATE_LIMIT_MESSAGE_ZH}`" />
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>

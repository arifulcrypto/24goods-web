<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import { TURNSTILE_ACTIONS, type AdminSiteV5 } from '../../api-v5'
import { secretChange, secretFromServer, type SecretField } from '../../lib/secret'
import { useSave } from '../../composables/useSave'
import SecretInput from '../SecretInput.vue'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const errors = ref<Record<string, string>>({})
const f = reactive({ ts_on: false, site_key: '', actions: [] as string[], verify: false, g_id: '', ips: '' })
const tsSecret = ref<SecretField>(secretFromServer())
const gSecret = ref<SecretField>(secretFromServer())
watch(
  () => props.site,
  (s) => {
    const x = s.security
    f.ts_on = x?.turnstile_enabled ?? false
    f.site_key = x?.turnstile_site_key ?? ''
    f.actions = [...(x?.turnstile_actions ?? [])]
    f.verify = x?.email_verify_on_register ?? false
    f.g_id = x?.google_client_id ?? ''
    f.ips = (x?.staff_ip_allowlist ?? []).join('\n')
    tsSecret.value = secretFromServer(s.secrets?.turnstile_secret)
    gSecret.value = secretFromServer(s.secrets?.google_client_secret)
  },
  { immediate: true },
)
async function save() {
  const e: Record<string, string> = {}
  const willHaveSecret = (s: SecretField) => (s.mode === 'replace' ? !!s.value.trim() : s.mode === 'clear' ? false : s.configured)
  if (f.ts_on && !f.site_key.trim()) e.site_key = t('v5.security.siteKeyRequired')
  if (f.ts_on && !willHaveSecret(tsSecret.value)) e.ts_secret = t('v5.security.secretRequired')
  const ips = f.ips.split('\n').map((x) => x.trim()).filter(Boolean)
  if (ips.length > 50 || ips.some((x) => !/^[0-9a-fA-F:.]+(\/\d{1,3})?$/.test(x))) e.ips = t('v5.security.staffIpsInvalid')
  errors.value = e
  if (Object.keys(e).length) return
  const r = await run(async () => {
    for (const [name, field] of [['turnstile_secret', tsSecret.value], ['google_client_secret', gSecret.value]] as const) {
      const ch = secretChange(field)
      if (ch) await apiV5.site.setSecret(name, ch.value)
    }
    return apiV5.site.put({
      security: {
        turnstile_enabled: f.ts_on,
        turnstile_site_key: f.site_key.trim(),
        turnstile_actions: f.actions,
        email_verify_on_register: f.verify,
        google_client_id: f.g_id.trim(),
        staff_ip_allowlist: ips,
      },
    })
  })
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.security.turnstile') }}</h2>
      <p class="muted small">{{ t('v5.security.turnstileNote') }}</p>
      <el-form-item :label="t('v5.security.turnstileOn')"><el-switch v-model="f.ts_on" /></el-form-item>
      <el-form-item :label="t('v5.security.siteKey')" :error="errors.site_key"><el-input v-model="f.site_key" maxlength="100" spellcheck="false" /></el-form-item>
      <SecretInput v-model="tsSecret" :label="t('v5.security.secretKey')" />
      <div v-if="errors.ts_secret" class="el-form-item__error static">{{ errors.ts_secret }}</div>
      <el-form-item :label="t('v5.security.actions')">
        <el-checkbox-group v-model="f.actions">
          <el-checkbox v-for="a in TURNSTILE_ACTIONS" :key="a" :value="a">{{ t(`v5.security.action.${a}`) }}</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.security.staffIps') }}</h2>
      <p class="muted small">{{ t('v5.security.staffIpsNote') }}</p>
      <el-form-item :error="errors.ips">
        <el-input v-model="f.ips" type="textarea" :rows="4" spellcheck="false" placeholder="203.0.113.7&#10;198.51.100.0/24" />
      </el-form-item>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.security.emailVerify') }}</h2>
      <el-form-item :label="t('v5.security.emailVerifyOn')">
        <el-switch v-model="f.verify" />
        <div class="muted small full">{{ t('v5.security.emailVerifyHint') }}</div>
      </el-form-item>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.security.google') }}</h2>
      <p class="muted small">{{ t('v5.security.googleNote') }}</p>
      <el-form-item :label="t('v5.security.googleId')"><el-input v-model="f.g_id" maxlength="200" spellcheck="false" /></el-form-item>
      <SecretInput v-model="gSecret" :label="t('v5.security.googleSecret')" />
      <p class="muted small mono">{{ t('v5.security.redirectUri') }} {{ '<origin>' }}/api/v1/auth/oauth/google/callback</p>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>
<style scoped>
.static {
  position: static;
  margin: -8px 0 12px;
}
.full {
  width: 100%;
}
</style>

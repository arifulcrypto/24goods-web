<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import type { AdminSiteV5 } from '../../api-v5'
import { secretChange, secretFromServer, type SecretField } from '../../lib/secret'
import { useSave } from '../../composables/useSave'
import { toastError, toastOk } from '../../ui'
import SecretInput from '../SecretInput.vue'
import EmailTemplatesEditor from './EmailTemplatesEditor.vue'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const errors = ref<Record<string, string>>({})
const f = reactive({
  enabled: false, host: '', port: 587, username: '', from_addr: '', from_name: '',
  security: 'starttls' as 'starttls' | 'ssl' | 'none', paid: true, delivered: true,
})
const pw = ref<SecretField>(secretFromServer())
const testTo = ref('')
const testing = ref(false)
watch(
  () => props.site,
  (s) => {
    const x = s.email
    f.enabled = x?.enabled ?? false
    f.host = x?.host ?? ''
    f.port = x?.port ?? 587
    f.username = x?.username ?? ''
    f.from_addr = x?.from_addr ?? ''
    f.from_name = x?.from_name ?? ''
    f.security = x?.security ?? 'starttls'
    f.paid = x?.notify_order_paid ?? true
    f.delivered = x?.notify_order_delivered ?? true
    pw.value = secretFromServer(s.secrets?.smtp_password)
  },
  { immediate: true },
)
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
async function save() {
  const e: Record<string, string> = {}
  if (f.enabled && !f.host.trim()) e.host = t('v5.email.hostRequired')
  if (f.enabled && !EMAIL_RE.test(f.from_addr.trim())) e.from = t('v5.email.fromInvalid')
  errors.value = e
  if (Object.keys(e).length) return
  const r = await run(async () => {
    const ch = secretChange(pw.value)
    if (ch) await apiV5.site.setSecret('smtp_password', ch.value)
    return apiV5.site.put({
      email: {
        enabled: f.enabled, host: f.host.trim(), port: f.port, username: f.username.trim(), from_addr: f.from_addr.trim(),
        from_name: f.from_name.trim(), security: f.security, notify_order_paid: f.paid, notify_order_delivered: f.delivered,
      },
    })
  })
  if (r) emit('saved', r)
}
async function sendTest() {
  if (!EMAIL_RE.test(testTo.value.trim())) {
    errors.value = { ...errors.value, to: t('v5.email.toInvalid') }
    return
  }
  errors.value = { ...errors.value, to: '' }
  testing.value = true
  try {
    const r = await apiV5.site.testEmail(testTo.value.trim())
    if (r.ok) toastOk(t('v5.email.testOk'))
    else ElMessage.error({ message: `${t('v5.email.testFailed')}${r.error ? `: ${r.error}` : ''}`, grouping: true })
  } catch (err) {
    toastError(err)
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.email.title') }}</h2>
      <p class="muted small">{{ t('v5.email.note') }}</p>
      <el-form-item :label="t('v5.email.enabled')"><el-switch v-model="f.enabled" /></el-form-item>
      <div class="grid">
        <el-form-item :label="t('v5.email.host')" :error="errors.host"><el-input v-model="f.host" maxlength="255" spellcheck="false" placeholder="smtp.example.com" /></el-form-item>
        <el-form-item :label="t('v5.email.port')"><el-input-number v-model="f.port" :min="1" :max="65535" :controls="false" /></el-form-item>
        <el-form-item :label="t('v5.email.security')">
          <el-select v-model="f.security">
            <el-option value="starttls" label="STARTTLS" />
            <el-option value="ssl" label="SSL/TLS" />
            <el-option value="none" :label="t('v5.email.noTls')" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('v5.email.username')"><el-input v-model="f.username" maxlength="255" autocomplete="off" spellcheck="false" /></el-form-item>
      </div>
      <SecretInput v-model="pw" :label="t('v5.email.password')" />
      <div class="grid">
        <el-form-item :label="t('v5.email.fromAddr')" :error="errors.from"><el-input v-model="f.from_addr" maxlength="255" inputmode="email" /></el-form-item>
        <el-form-item :label="t('v5.email.fromName')"><el-input v-model="f.from_name" maxlength="100" /></el-form-item>
      </div>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.email.notify') }}</h2>
      <div class="opt"><span>{{ t('v5.email.notifyPaid') }}</span><el-switch v-model="f.paid" /></div>
      <div class="opt"><span>{{ t('v5.email.notifyDelivered') }}</span><el-switch v-model="f.delivered" /></div>
      <p class="muted small">{{ t('v5.email.notifyNote') }}</p>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
    <section class="panel block test">
      <h2 class="h2">{{ t('v5.email.test') }}</h2>
      <p class="muted small">{{ t('v5.email.testNote') }}</p>
      <el-form-item :error="errors.to">
        <el-input v-model="testTo" inputmode="email" :placeholder="t('v5.email.testTo')" style="max-width: 320px" />
        <el-button :loading="testing" style="margin-left: 8px" @click="sendTest">{{ t('v5.email.sendTest') }}</el-button>
      </el-form-item>
    </section>
  </el-form>
  <EmailTemplatesEditor :site="site" @saved="emit('saved', $event)" />
</template>

<style scoped src="./settings.css"></style>
<style scoped>
.test {
  margin-top: 16px;
}
</style>

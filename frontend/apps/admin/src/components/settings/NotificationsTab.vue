<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiV5 } from '../../api'
import { TG_EVENTS, type AdminSiteV5 } from '../../api-v5'
import { secretChange, secretFromServer, type SecretField } from '../../lib/secret'
import { useSave } from '../../composables/useSave'
import { toastError, toastOk } from '../../ui'
import SecretInput from '../SecretInput.vue'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const errors = ref<Record<string, string>>({})
const testing = ref(false)
const f = reactive({ enabled: false, chats: '', events: {} as Record<string, boolean>, buyerSound: true, buyerDesktop: false })
const token = ref<SecretField>(secretFromServer())
watch(
  () => props.site,
  (s) => {
    const n = s.notifications
    f.enabled = n?.telegram_enabled ?? false
    f.chats = (n?.telegram_chat_ids ?? []).join('\n')
    f.buyerSound = n?.buyer_sound ?? true
    f.buyerDesktop = n?.buyer_desktop ?? false
    for (const k of TG_EVENTS) f.events[k] = n?.[`event_${k}`] ?? true
    token.value = secretFromServer(s.secrets?.telegram_bot_token)
  },
  { immediate: true },
)
const ids = () => f.chats.split('\n').map((x) => x.trim()).filter(Boolean)
async function save() {
  const e: Record<string, string> = {}
  const list = ids()
  if (list.length > 10 || list.some((x) => !/^-?\d{1,20}$/.test(x))) e.chats = t('v5.notify.chatsInvalid')
  const s = token.value
  const has = s.mode === 'replace' ? !!s.value.trim() : s.mode === 'clear' ? false : s.configured
  if (f.enabled && !has) e.token = t('v5.notify.tokenRequired')
  errors.value = e
  if (Object.keys(e).length) return
  const r = await run(async () => {
    const ch = secretChange(token.value)
    if (ch) await apiV5.site.setSecret('telegram_bot_token', ch.value)
    return apiV5.site.put({
      notifications: {
        telegram_enabled: f.enabled,
        telegram_chat_ids: list,
        event_order_paid: f.events.order_paid,
        event_chat_message: f.events.chat_message,
        event_after_sales: f.events.after_sales,
        event_payment_review: f.events.payment_review,
        event_reseller_application: f.events.reseller_application,
        event_low_stock: f.events.low_stock,
        buyer_sound: f.buyerSound,
        buyer_desktop: f.buyerDesktop,
      },
    })
  })
  if (r) emit('saved', r)
}
async function sendTest() {
  testing.value = true
  try {
    const r = await apiV5.site.testTelegram()
    if (r.ok) toastOk(t('v5.notify.testOk'))
    else {
      const key = `v5.notify.err.${r.error}`
      ElMessage.error({ message: `${t('v5.notify.testFailed')}: ${r.error ? t(key) : ''}`, grouping: true })
    }
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
      <h2 class="h2">{{ t('v5.notify.title') }}</h2>
      <p class="muted small">{{ t('v5.notify.note') }}</p>
      <el-form-item :label="t('v5.notify.enabled')"><el-switch v-model="f.enabled" /></el-form-item>
      <SecretInput v-model="token" :label="t('v5.notify.token')" />
      <div v-if="errors.token" class="el-form-item__error static">{{ errors.token }}</div>
      <el-form-item :label="t('v5.notify.chats')" :error="errors.chats">
        <el-input v-model="f.chats" type="textarea" :rows="3" spellcheck="false" placeholder="123456789&#10;-1001234567890" />
        <div class="muted small full">{{ t('v5.notify.chatsHint') }}</div>
      </el-form-item>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.notify.events') }}</h2>
      <el-form-item v-for="k in TG_EVENTS" :key="k">
        <el-switch v-model="f.events[k]" :active-text="t(`v5.notify.ev.${k}`)" />
      </el-form-item>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.notify.buyerTitle') }}</h2>
      <p class="muted small">{{ t('v5.notify.buyerNote') }}</p>
      <el-form-item><el-switch v-model="f.buyerSound" :active-text="t('v5.notify.buyerSound')" /></el-form-item>
      <el-form-item><el-switch v-model="f.buyerDesktop" :active-text="t('v5.notify.buyerDesktop')" /></el-form-item>
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
    <el-button :loading="testing" @click="sendTest">{{ t('v5.notify.test') }}</el-button>
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

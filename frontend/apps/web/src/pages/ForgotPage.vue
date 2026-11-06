<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import AuthShell from '../components/AuthShell.vue'
import TurnstileBox from '../components/TurnstileBox.vue'
import { useTurnstile } from '../composables/useTurnstile'
import { api } from '../api'
import { showSuccessToast, toastError } from '../ui'

/** review-v5 §5.2: email → code (purpose reset) → code + new password → POST /auth/password-reset. */
const { t } = useI18n()
const router = useRouter()
const ts = useTurnstile('password_reset')
const email = ref('')
const code = ref('')
const password = ref('')
const show = ref(false)
const step = ref<'email' | 'reset'>('email')
const busy = ref(false)
const cooldown = ref(0)
let timer: ReturnType<typeof setInterval> | null = null
onBeforeUnmount(() => timer && clearInterval(timer))
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function startCooldown() {
  cooldown.value = 60
  timer ??= setInterval(() => {
    cooldown.value = Math.max(0, cooldown.value - 1)
    if (!cooldown.value && timer) {
      clearInterval(timer)
      timer = null
    }
  }, 1000)
}
async function sendCode() {
  if (busy.value || cooldown.value > 0) return
  busy.value = true
  try {
    const token = ts.require()
    // 204 always: the response never reveals whether the address has an account
    await api.auth.emailCode({ email: email.value.trim(), purpose: 'reset', ...(token ? { turnstile_token: token } : {}) })
    step.value = 'reset'
    showSuccessToast(t('auth.codeSent'))
    startCooldown()
  } catch (e) {
    toastError(e)
  } finally {
    ts.reset()
    busy.value = false
  }
}
async function reset() {
  if (busy.value) return
  busy.value = true
  try {
    const token = ts.require()
    await api.auth.passwordReset({
      email: email.value.trim(),
      code: code.value.trim(),
      new_password: password.value,
      ...(token ? { turnstile_token: token } : {}),
    })
    showSuccessToast(t('auth.resetDone'))
    await router.replace({ name: 'login' })
  } catch (e) {
    toastError(e)
  } finally {
    ts.reset()
    password.value = ''
    busy.value = false
  }
}
</script>

<template>
  <AuthShell :title="t('auth.forgotTitle')" :subtitle="t('auth.forgotSubtitle')">
    <van-form @submit="step === 'email' ? sendCode() : reset()">
      <van-field
        v-model="email"
        name="email"
        type="email"
        autocomplete="email"
        inputmode="email"
        :readonly="step === 'reset'"
        :label="t('auth.email')"
        :placeholder="t('auth.emailPlaceholder')"
        :rules="[{ validator: (v: string) => EMAIL_RE.test(v.trim()), message: t('auth.invalidEmail') }]"
      />
      <template v-if="step === 'reset'">
        <van-field
          v-model="code"
          name="code"
          inputmode="numeric"
          autocomplete="one-time-code"
          maxlength="6"
          :label="t('auth.code')"
          :placeholder="t('auth.codePlaceholder')"
          :rules="[{ validator: (v: string) => /^\d{6}$/.test(v.trim()), message: t('auth.codeInvalid') }]"
        >
          <template #button>
            <van-button size="small" type="primary" plain native-type="button" :disabled="cooldown > 0" @click="sendCode">
              {{ cooldown > 0 ? t('auth.resendIn', { n: cooldown }) : t('auth.sendCode') }}
            </van-button>
          </template>
        </van-field>
        <van-field
          v-model="password"
          name="password"
          :type="show ? 'text' : 'password'"
          autocomplete="new-password"
          :label="t('auth.newPassword')"
          :placeholder="t('auth.passwordHint')"
          :right-icon="show ? 'eye-o' : 'closed-eye'"
          :rules="[{ validator: (v: string) => v.length >= 10, message: t('auth.passwordTooShort') }]"
          @click-right-icon="show = !show"
        />
      </template>
      <TurnstileBox :ts="ts" class="ts" />
      <div class="submit">
        <van-button round block type="primary" native-type="submit" :loading="busy">
          {{ step === 'email' ? t('auth.sendCode') : t('auth.resetPassword') }}
        </van-button>
      </div>
    </van-form>
    <template #foot>
      <RouterLink :to="{ name: 'login' }" class="link">{{ t('auth.backToLogin') }}</RouterLink>
    </template>
  </AuthShell>
</template>

<style scoped>
.ts {
  padding: 0 16px;
}
.submit {
  padding: 16px 16px 0;
}
.link {
  display: inline-block;
  padding: 10px 4px;
  font-weight: 700;
  text-decoration: none;
}
</style>

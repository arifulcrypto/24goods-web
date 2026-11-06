<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ApiError, isApiError, safeRedirect } from '@24goods/shared'
import { api } from '../api'
import AuthShell from '../components/AuthShell.vue'
import PowStatus from '../components/PowStatus.vue'
import TurnstileBox from '../components/TurnstileBox.vue'
import { useTurnstile } from '../composables/useTurnstile'
import { useSiteStore } from '../stores/site'
import { isPowAbort, usePow } from '../composables/usePow'
import { useAuthStore } from '../stores/auth'
import { currentLocale } from '../i18n'
import { showSuccessToast, toastError } from '../ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const pow = usePow()
const ts = useTurnstile('register')
const site = useSiteStore()
const email = ref('')
const displayName = ref('')
const password = ref('')
const show = ref(false)
const busy = ref(false)
// v5 §5.2: email code step (shown when the site demands it, or when the server answers email_code_required)
const code = ref('')
const needCode = ref(site.site?.email_verify_on_register === true)
const sending = ref(false)
const cooldown = ref(0)
let timer: ReturnType<typeof setInterval> | null = null
onBeforeUnmount(() => timer && clearInterval(timer))
// v5 §3: login terms (checkbox) — links to the published pages
const legal = computed(() => site.legal)
const agree = ref(false)
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

async function sendCode() {
  if (sending.value || cooldown.value > 0 || !EMAIL_RE.test(email.value.trim())) return
  sending.value = true
  try {
    const token = ts.require()
    await api.auth.emailCode({ email: email.value.trim(), purpose: 'register', ...(token ? { turnstile_token: token } : {}) })
    showSuccessToast(t('auth.codeSent'))
    cooldown.value = 60
    timer ??= setInterval(() => {
      cooldown.value = Math.max(0, cooldown.value - 1)
      if (!cooldown.value && timer) {
        clearInterval(timer)
        timer = null
      }
    }, 1000)
  } catch (e) {
    toastError(e)
  } finally {
    sending.value = false
    ts.reset()
  }
}

async function submit() {
  if (busy.value) return
  if (legal.value && !agree.value) {
    toastError(new ApiError('terms_required', 'terms', 422))
    return
  }
  busy.value = true
  try {
    const token = ts.require()
    const solution = await pow.run('register')
    await auth.register({
      pow: solution,
      email: email.value.trim(),
      password: password.value,
      display_name: displayName.value.trim(),
      locale: currentLocale(),
      ...(needCode.value && code.value.trim() ? { email_code: code.value.trim() } : {}),
      ...(legal.value ? { agree_terms: true } : {}),
      ...(token ? { turnstile_token: token } : {}),
    })
    showSuccessToast(t('auth.registered'))
    await router.replace(safeRedirect(route.query.redirect, '/'))
  } catch (e) {
    if (isApiError(e) && e.code === 'email_code_required') needCode.value = true
    if (!isPowAbort(e)) toastError(e)
  } finally {
    ts.reset()
    busy.value = false
  }
}
</script>

<template>
  <AuthShell :title="t('auth.registerTitle')" :subtitle="t('auth.registerSubtitle')">
    <van-form @submit="submit">
      <van-field
        v-model="email"
        name="email"
        type="email"
        autocomplete="email"
        inputmode="email"
        :label="t('auth.email')"
        :placeholder="t('auth.emailPlaceholder')"
        :rules="[{ validator: (v: string) => EMAIL_RE.test(v.trim()), message: t('auth.invalidEmail') }]"
      />
      <van-field
        v-model="displayName"
        name="display_name"
        autocomplete="nickname"
        maxlength="40"
        :label="t('auth.displayName')"
        :placeholder="t('auth.displayNamePlaceholder')"
        :rules="[{ validator: (v: string) => v.trim().length > 0, message: t('common.required') }]"
      />
      <van-field
        v-model="password"
        name="password"
        :type="show ? 'text' : 'password'"
        autocomplete="new-password"
        :label="t('auth.password')"
        :placeholder="t('auth.passwordHint')"
        :right-icon="show ? 'eye-o' : 'closed-eye'"
        :rules="[{ validator: (v: string) => v.length >= 10, message: t('auth.passwordTooShort') }]"
        @click-right-icon="show = !show"
      />
      <van-field
        v-if="needCode"
        v-model="code"
        name="email_code"
        inputmode="numeric"
        autocomplete="one-time-code"
        maxlength="6"
        :label="t('auth.code')"
        :placeholder="t('auth.codePlaceholder')"
        :rules="[{ validator: (v: string) => /^\d{6}$/.test(v.trim()), message: t('auth.codeInvalid') }]"
      >
        <template #button>
          <van-button size="small" type="primary" plain native-type="button" :loading="sending" :disabled="cooldown > 0" @click="sendCode">
            {{ cooldown > 0 ? t('auth.resendIn', { n: cooldown }) : t('auth.sendCode') }}
          </van-button>
        </template>
      </van-field>
      <div v-if="legal" class="terms">
        <van-checkbox v-model="agree" shape="square" icon-size="20">
          <i18n-t keypath="auth.agreeTerms" tag="span">
            <template #terms>
              <template v-for="(pg, i) in legal.pages" :key="pg.slug">
                <span v-if="i">, </span>
                <RouterLink :to="`/a/${encodeURIComponent(pg.slug)}`" target="_blank" class="tlink" @click.stop>{{ pg.title }}</RouterLink>
              </template>
            </template>
          </i18n-t>
        </van-checkbox>
      </div>
      <TurnstileBox :ts="ts" class="ts" />
      <div class="submit">
        <PowStatus :solving="pow.solving.value" :progress="pow.progress.value" />
        <van-button round block type="primary" native-type="submit" :loading="busy">{{ t('me.register') }}</van-button>
      </div>
    </van-form>
    <template #foot>
      <span class="muted">{{ t('auth.haveAccount') }}</span>
      <RouterLink :to="{ name: 'login', query: route.query }" class="link">{{ t('me.login') }}</RouterLink>
    </template>
  </AuthShell>
</template>

<style scoped>
.submit {
  padding: 16px 16px 0;
}
.link {
  display: inline-block;
  margin-left: 6px;
  padding: 10px 4px;
  font-weight: 700;
  text-decoration: none;
}
.ts {
  padding: 0 16px;
}
.terms {
  padding: 12px 16px 0;
  font-size: 13px;
  line-height: 1.5;
}
.tlink {
  text-decoration: underline;
}
</style>

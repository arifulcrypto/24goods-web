<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { isApiError, safeRedirect } from '@24goods/shared'
import AuthShell from '../components/AuthShell.vue'
import PowStatus from '../components/PowStatus.vue'
import { isPowAbort, usePow } from '../composables/usePow'
import { useAuthStore } from '../stores/auth'
import { showFailToast, showSuccessToast, toastError } from '../ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const pow = usePow()
const email = ref('')
const password = ref('')
const show = ref(false)
const busy = ref(false)
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

async function submit() {
  if (busy.value) return
  busy.value = true
  try {
    try {
      await auth.login(email.value.trim(), password.value)
    } catch (e) {
      // after repeated failures the server asks for a proof-of-work (review-v3 §3)
      if (!(isApiError(e) && e.code === 'pow_required')) throw e
      await auth.login(email.value.trim(), password.value, await pow.run('login'))
    }
    showSuccessToast(t('auth.loggedIn'))
    await router.replace(safeRedirect(route.query.redirect, '/'))
  } catch (e) {
    if (isPowAbort(e)) return
    if (isApiError(e) && e.code === 'unauthorized') showFailToast(t('auth.loginFailed'))
    else toastError(e)
  } finally {
    busy.value = false
    password.value = ''
  }
}
</script>

<template>
  <AuthShell :title="t('auth.loginTitle')" :subtitle="t('auth.loginSubtitle')">
    <van-form @submit="submit">
      <van-field
        v-model="email"
        name="email"
        type="email"
        autocomplete="username"
        inputmode="email"
        :label="t('auth.email')"
        :placeholder="t('auth.emailPlaceholder')"
        :rules="[{ validator: (v: string) => EMAIL_RE.test(v.trim()), message: t('auth.invalidEmail') }]"
      />
      <van-field
        v-model="password"
        name="password"
        :type="show ? 'text' : 'password'"
        autocomplete="current-password"
        :label="t('auth.password')"
        :placeholder="t('auth.password')"
        :right-icon="show ? 'eye-o' : 'closed-eye'"
        :rules="[{ required: true, message: t('common.required') }]"
        @click-right-icon="show = !show"
      />
      <div class="submit">
        <PowStatus :solving="pow.solving.value" :progress="pow.progress.value" />
        <van-button round block type="primary" native-type="submit" :loading="busy">{{ t('me.login') }}</van-button>
      </div>
    </van-form>
    <template #foot>
      <span class="muted">{{ t('auth.noAccount') }}</span>
      <RouterLink :to="{ name: 'register', query: route.query }" class="link">{{ t('me.register') }}</RouterLink>
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
</style>

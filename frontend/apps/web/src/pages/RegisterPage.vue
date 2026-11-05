<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { safeRedirect } from '@24goods/shared'
import AuthShell from '../components/AuthShell.vue'
import { useAuthStore } from '../stores/auth'
import { currentLocale } from '../i18n'
import { showSuccessToast, toastError } from '../ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const email = ref('')
const displayName = ref('')
const password = ref('')
const show = ref(false)
const busy = ref(false)
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

async function submit() {
  if (busy.value) return
  busy.value = true
  try {
    await auth.register({
      email: email.value.trim(),
      password: password.value,
      display_name: displayName.value.trim(),
      locale: currentLocale(),
    })
    showSuccessToast(t('auth.registered'))
    await router.replace(safeRedirect(route.query.redirect, '/'))
  } catch (e) {
    toastError(e)
  } finally {
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
      <div class="submit">
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
</style>

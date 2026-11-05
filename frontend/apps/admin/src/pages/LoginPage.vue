<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { LogoMark } from '@24goods/shared/ui'
import { isApiError, safeRedirect } from '@24goods/shared'
import { useAuthStore } from '../stores/auth'
import { setLocale } from '../locale'
import { toastError } from '../ui'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const form = reactive({ email: '', password: '' })
const busy = ref(false)
const error = ref('')

async function submit() {
  if (busy.value || !form.email || !form.password) return
  busy.value = true
  error.value = ''
  try {
    await auth.login(form.email.trim(), form.password)
    await router.replace(safeRedirect(route.query.redirect, '/'))
  } catch (e) {
    if (isApiError(e) && e.code === 'not_staff') error.value = t('login.notStaff')
    else if (isApiError(e) && e.code === 'unauthorized') error.value = t('login.failed')
    else toastError(e)
  } finally {
    busy.value = false
    form.password = ''
  }
}
</script>

<template>
  <div class="login">
    <div class="login-card">
      <div class="brand"><LogoMark :size="36" />{{ t('app.title') }}</div>
      <h1>{{ t('login.title') }}</h1>
      <p class="muted">{{ t('login.subtitle') }}</p>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="err" />
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item :label="t('login.email')">
          <el-input v-model="form.email" type="email" autocomplete="username" size="large" />
        </el-form-item>
        <el-form-item :label="t('login.password')">
          <el-input v-model="form.password" type="password" autocomplete="current-password" show-password size="large" />
        </el-form-item>
        <el-button type="primary" native-type="submit" size="large" :loading="busy" class="submit">{{ t('login.submit') }}</el-button>
      </el-form>
      <div class="lang">
        <el-radio-group :model-value="locale" size="small" @update:model-value="(v) => setLocale(v as 'en' | 'zh-CN')">
          <el-radio-button value="en">{{ t('lang.en') }}</el-radio-button>
          <el-radio-button value="zh-CN">{{ t('lang.zh') }}</el-radio-button>
        </el-radio-group>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 16px;
  background: radial-gradient(circle at 10% 10%, #e0e7ff, transparent 40%), var(--a-bg);
}
.login-card {
  width: min(400px, 100%);
  background: var(--a-surface);
  border: 1px solid var(--a-line);
  border-radius: 14px;
  padding: 28px;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  margin-bottom: 18px;
}
h1 {
  font-size: 22px;
  margin: 0;
}
p {
  margin: 4px 0 18px;
}
.err {
  margin-bottom: 14px;
}
.submit {
  width: 100%;
  margin-top: 4px;
}
.lang {
  margin-top: 18px;
  display: flex;
  justify-content: center;
}
</style>

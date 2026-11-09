<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { compressUploads, goBack, imageQuality, isApiError, setMediaPreference, type LocalePreference } from '@24goods/shared'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { languagePreference, setLocale } from '../locale'
import { showSuccessToast, showToast, toastError } from '../ui'

/** Issue #38 / #48 §2.3: preferences and account security moved off the My Account list. */
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const saving = ref(false)

function switchLang(l: LocalePreference) {
  setLocale(l)
}
function setQuality(v: string) {
  setMediaPreference(v as 'auto' | 'low' | 'high', compressUploads.value)
}

// ---- change credentials / secure account ----
const credShow = ref(false)
const credEmail = ref('')
const credCurrent = ref('')
const credNew = ref('')
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const credValid = computed(() => {
  const emailOk = !credEmail.value.trim() || EMAIL_RE.test(credEmail.value.trim())
  const pwOk = !credNew.value || credNew.value.length >= 10
  const changed = !!credEmail.value.trim() || !!credNew.value
  const currentOk = auth.user?.is_instant || !credNew.value || !!credCurrent.value
  return emailOk && pwOk && changed && !!currentOk
})
function openCred() {
  credEmail.value = auth.user?.email ?? ''
  credCurrent.value = ''
  credNew.value = ''
  credShow.value = true
}
async function saveCred() {
  if (!credValid.value || saving.value) return
  saving.value = true
  try {
    const body: { current_password?: string; email?: string; new_password?: string } = {}
    if (credEmail.value.trim() && credEmail.value.trim() !== auth.user?.email) body.email = credEmail.value.trim()
    if (credNew.value) body.new_password = credNew.value
    if (credCurrent.value) body.current_password = credCurrent.value
    auth.setUser(await api.me.credentials(body))
    credShow.value = false
    showSuccessToast(t('common.saved'))
  } catch (e) {
    if (isApiError(e) && (e.code === 'invalid_password' || e.code === 'unauthorized' || e.code === 'forbidden')) showToast(t('me.wrongPassword'))
    else toastError(e)
  } finally {
    credCurrent.value = ''
    credNew.value = ''
    saving.value = false
  }
}

// ---- delete account ----
const delShow = ref(false)
const delConfirm = ref('')
const delPassword = ref('')
const delBlocked = ref(false)
function openDelete() {
  delConfirm.value = ''
  delPassword.value = ''
  delBlocked.value = false
  delShow.value = true
}
async function deleteAccount() {
  if (delConfirm.value !== 'DELETE' || saving.value) return
  saving.value = true
  delBlocked.value = false
  try {
    await api.me.remove({ confirm: 'DELETE', ...(delPassword.value ? { password: delPassword.value } : {}) })
    auth.clear()
    delShow.value = false
    showToast(t('me.deleted'))
    void router.replace('/')
  } catch (e) {
    if (isApiError(e) && e.status === 409) delBlocked.value = true
    else toastError(e)
  } finally {
    delPassword.value = ''
    saving.value = false
  }
}

// My Account's "secure your account" card links here with ?secure=1
onMounted(() => {
  if (route.query.secure === '1' && auth.user) openCred()
})
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('me.settings')" class="dock-top" left-arrow fixed placeholder safe-area-inset-top @click-left="goBack(router, '/me')" />

    <section class="section card">
      <h2 class="group-title">{{ t('me.preferences') }}</h2>
      <van-cell-group :border="false">
        <van-cell :title="t('me.language')" icon="flag-o" center>
          <template #value>
            <div class="seg" role="radiogroup" :aria-label="t('me.language')">
              <button type="button" role="radio" :aria-checked="languagePreference === 'auto'" :class="{ on: languagePreference === 'auto' }" @click="switchLang('auto')">{{ t('lang.auto') }}</button>
              <button type="button" role="radio" :aria-checked="languagePreference === 'en'" :class="{ on: languagePreference === 'en' }" @click="switchLang('en')">{{ t('lang.en') }}</button>
              <button type="button" role="radio" :aria-checked="languagePreference === 'zh-CN'" :class="{ on: languagePreference === 'zh-CN' }" @click="switchLang('zh-CN')">{{ t('lang.zh') }}</button>
            </div>
          </template>
        </van-cell>
        <van-cell :title="t('mediaPrefs.quality')" icon="photo-o" center>
          <template #value>
            <select class="select" :aria-label="t('mediaPrefs.quality')" :value="imageQuality" @change="setQuality(($event.target as HTMLSelectElement).value)">
              <option value="auto">{{ t('mediaPrefs.auto') }}</option>
              <option value="low">{{ t('mediaPrefs.low') }}</option>
              <option value="high">{{ t('mediaPrefs.high') }}</option>
            </select>
          </template>
        </van-cell>
        <van-cell :title="t('mediaPrefs.compress')" icon="upgrade" center>
          <template #value>
            <van-switch :model-value="compressUploads" :aria-label="t('mediaPrefs.compress')" @update:model-value="(v: boolean) => setMediaPreference(imageQuality, v)" />
          </template>
        </van-cell>
      </van-cell-group>
    </section>

    <section v-if="auth.user" class="section card">
      <h2 class="group-title">{{ t('me.accountSecurity') }}</h2>
      <van-cell-group :border="false">
        <van-cell :title="auth.user.is_instant ? t('me.secureTitle') : t('me.changeCredentials')" icon="lock" is-link @click="openCred" />
        <van-cell :title="t('me.deleteAccount')" icon="delete-o" is-link class="danger" @click="openDelete" />
      </van-cell-group>
    </section>

    <van-popup v-model:show="credShow" position="bottom" round closeable safe-area-inset-bottom teleport="body" class="buy-sheet">
      <form class="sheet" @submit.prevent="saveCred">
        <h2>{{ auth.user?.is_instant ? t('me.secureTitle') : t('me.changeCredentials') }}</h2>
        <van-field v-model="credEmail" type="email" inputmode="email" autocomplete="email" :label="t('auth.email')" :placeholder="t('auth.emailPlaceholder')" />
        <van-field v-if="!auth.user?.is_instant" v-model="credCurrent" type="password" autocomplete="current-password" :label="t('me.currentPassword')" />
        <van-field v-model="credNew" type="password" autocomplete="new-password" :label="t('me.newPassword')" :placeholder="t('auth.passwordHint')" />
        <van-button block round type="primary" size="large" native-type="submit" :loading="saving" :disabled="!credValid">{{ t('common.save') }}</van-button>
      </form>
    </van-popup>

    <van-popup v-model:show="delShow" position="bottom" round closeable safe-area-inset-bottom teleport="body" class="buy-sheet">
      <form class="sheet" @submit.prevent="deleteAccount">
        <h2>{{ t('me.deleteAccount') }}</h2>
        <p class="del-text">{{ t('me.deleteDeleted') }}</p>
        <p class="del-text">{{ t('me.deleteRetained') }}</p>
        <p v-if="delBlocked" class="del-err" role="alert">{{ t('me.deleteBlocked') }}</p>
        <van-field v-if="!auth.user?.is_instant" v-model="delPassword" type="password" autocomplete="current-password" :label="t('auth.password')" />
        <van-field v-model="delConfirm" autocapitalize="characters" autocomplete="off" :label="t('me.typeDelete')" placeholder="DELETE" />
        <van-button block round type="danger" size="large" native-type="submit" :loading="saving" :disabled="delConfirm !== 'DELETE'">{{ t('me.deleteConfirm') }}</van-button>
      </form>
    </van-popup>
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
}
.card {
  overflow: hidden;
}
.group-title {
  margin: 0;
  padding: 12px 16px 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-2);
}
.seg {
  display: inline-flex;
  background: #f2f2f2;
  border-radius: 999px;
  padding: 2px;
}
.seg button {
  min-height: 36px;
  min-width: 56px;
  border: 0;
  border-radius: 999px;
  background: none;
  color: var(--c-text-2);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  position: relative;
}
.seg button::after {
  content: '';
  position: absolute;
  inset: -4px 0;
}
.seg button.on {
  background: var(--c-surface);
  color: var(--c-text);
  font-weight: 700;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}
.select {
  min-height: 44px;
  border: 0;
  background: none;
  font: inherit;
  color: var(--c-text);
}
.sheet {
  padding: 20px 16px 16px;
}
.sheet h2 {
  font-size: 18px;
  margin: 0 0 10px;
}
.del-text {
  font-size: 13px;
  color: var(--c-text-2);
  margin: 0 0 8px;
}
.del-err {
  color: var(--c-danger);
  font-size: 13px;
  margin: 0 0 8px;
}
:deep(.danger .van-cell__title) {
  color: var(--c-danger);
}
</style>

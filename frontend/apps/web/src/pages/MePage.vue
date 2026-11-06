<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { clearRecent, isApiError, recentIds, type Locale, type ProductSummary } from '@24goods/shared'
import { api } from '../api'
import MiniStrip from '../components/MiniStrip.vue'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { setLocale } from '../locale'
import type { AppLocale } from '../i18n'
import { confirm, showSuccessToast, showToast, toastError } from '../ui'

const { t, locale } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const convs = useConversationsStore()
const initial = computed(() => (auth.user?.display_name || auth.user?.handle || auth.user?.email || '?').trim().charAt(0).toUpperCase())
const identity = computed(() => auth.user?.email || auth.user?.handle || '')
const recent = ref<ProductSummary[]>([])

onMounted(async () => {
  const ids = recentIds()
  if (!ids.length) return
  try {
    recent.value = (await api.productsByIds(ids)).items
  } catch {
    /* optional */
  }
})
function clearViewed() {
  clearRecent()
  recent.value = []
}

function switchLang(l: AppLocale) {
  if (locale.value !== l) setLocale(l)
}
async function logout() {
  if (!(await confirm(t('me.logout'), t('me.logoutConfirm')))) return
  try {
    await auth.logout()
    showToast(t('me.loggedOut'))
    void router.replace('/')
  } catch (e) {
    toastError(e)
  }
}
function openSupport() {
  void router.push(auth.loggedIn ? '/chat' : { name: 'login', query: { redirect: '/chat' } })
}

// ---- edit profile ----
const editShow = ref(false)
const editName = ref('')
const editLocale = ref<Locale>('en')
const saving = ref(false)
function openEdit() {
  editName.value = auth.user?.display_name ?? ''
  editLocale.value = (auth.user?.locale ?? locale.value) as Locale
  editShow.value = true
}
async function saveProfile() {
  if (saving.value || !editName.value.trim()) return
  saving.value = true
  try {
    auth.setUser(await api.me.update({ display_name: editName.value.trim(), locale: editLocale.value }))
    setLocale(editLocale.value as AppLocale)
    editShow.value = false
    showSuccessToast(t('common.saved'))
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
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
</script>

<template>
  <div class="page with-tabbar">
    <header class="me-head">
      <template v-if="auth.user">
        <span class="avatar" aria-hidden="true">{{ initial }}</span>
        <div class="who">
          <div class="name">{{ auth.user.display_name }}</div>
          <div class="email">{{ identity }}</div>
        </div>
        <button type="button" class="edit" :aria-label="t('me.editProfile')" @click="openEdit"><van-icon name="edit" size="20" /></button>
      </template>
      <template v-else>
        <span class="avatar guest" aria-hidden="true"><van-icon name="user-o" size="28" /></span>
        <div class="who">
          <div class="name">{{ t('me.guest') }}</div>
          <div class="email">{{ t('me.signInHint') }}</div>
        </div>
      </template>
    </header>

    <div v-if="!auth.user" class="section auth-actions">
      <van-button round type="primary" block :to="{ name: 'login', query: { redirect: '/me' } }">{{ t('me.login') }}</van-button>
      <van-button round plain block :to="{ name: 'register', query: { redirect: '/me' } }">{{ t('me.register') }}</van-button>
    </div>

    <section v-if="auth.user?.is_instant" class="section card secure">
      <div>
        <strong>{{ t('me.secureTitle') }}</strong>
        <p class="muted">{{ t('me.secureText') }}</p>
      </div>
      <van-button round type="primary" size="small" class="tap" @click="openCred">{{ t('me.secureCta') }}</van-button>
    </section>

    <MiniStrip :title="t('home.recentlyViewed')" :items="recent" :clear-label="t('home.clear')" @clear="clearViewed" />

    <section class="section card">
      <van-cell-group :border="false">
        <van-cell v-if="auth.user" :title="t('me.orders')" icon="orders-o" is-link to="/orders" />
        <van-cell v-if="auth.user" :title="t('me.messages')" icon="chat-o" is-link to="/messages" :value="convs.totalUnread ? String(convs.totalUnread) : ''" />
        <van-cell v-if="auth.user" :title="t('me.changeCredentials')" icon="lock" is-link @click="openCred" />
        <van-cell :title="t('lookup.title')" icon="search" is-link to="/lookup" />
        <van-cell :title="t('help.title')" icon="question-o" is-link to="/help" />
        <van-cell :title="t('me.rules')" icon="description-o" is-link to="/a/rules" />
        <van-cell :title="t('me.afterSalesPolicy')" icon="shield-o" is-link to="/a/after-sales" />
        <van-cell :title="t('me.terms')" icon="notes-o" is-link to="/a/terms" />
        <van-cell :title="t('me.privacy')" icon="eye-o" is-link to="/a/privacy" />
        <van-cell :title="t('me.language')" icon="flag-o" center>
          <template #value>
            <div class="seg" role="radiogroup" :aria-label="t('me.language')">
              <button type="button" role="radio" :aria-checked="locale === 'en'" :class="{ on: locale === 'en' }" @click="switchLang('en')">
                {{ t('lang.en') }}
              </button>
              <button type="button" role="radio" :aria-checked="locale === 'zh-CN'" :class="{ on: locale === 'zh-CN' }" @click="switchLang('zh-CN')">
                {{ t('lang.zh') }}
              </button>
            </div>
          </template>
        </van-cell>
        <van-cell v-if="auth.isStaff" :title="t('me.staff')" :label="t('me.staffHint')" icon="manager-o" is-link url="/admin/" />
        <van-cell :title="t('me.contactCs')" icon="service-o" is-link @click="openSupport" />
        <van-cell v-if="auth.user" :title="t('me.deleteAccount')" icon="delete-o" is-link class="danger" @click="openDelete" />
      </van-cell-group>
    </section>

    <section class="section card tips">
      <div class="tips-title"><van-icon name="shield-o" /> {{ t('me.safety') }}</div>
      <p class="muted">{{ t('me.safetyText') }}</p>
    </section>

    <div v-if="auth.user" class="section">
      <van-button block round plain type="danger" @click="logout">{{ t('me.logout') }}</van-button>
    </div>

    <van-popup v-model:show="editShow" position="bottom" round closeable safe-area-inset-bottom teleport="body" class="buy-sheet">
      <form class="sheet" @submit.prevent="saveProfile">
        <h2>{{ t('me.editProfile') }}</h2>
        <van-field v-model="editName" maxlength="40" :label="t('auth.displayName')" :rules="[{ required: true }]" />
        <div class="lang-row">
          <span>{{ t('me.language') }}</span>
          <div class="seg" role="radiogroup" :aria-label="t('me.language')">
            <button type="button" role="radio" :aria-checked="editLocale === 'en'" :class="{ on: editLocale === 'en' }" @click="editLocale = 'en'">{{ t('lang.en') }}</button>
            <button type="button" role="radio" :aria-checked="editLocale === 'zh-CN'" :class="{ on: editLocale === 'zh-CN' }" @click="editLocale = 'zh-CN'">{{ t('lang.zh') }}</button>
          </div>
        </div>
        <van-button block round type="primary" size="large" native-type="submit" :loading="saving" :disabled="!editName.trim()">{{ t('common.save') }}</van-button>
      </form>
    </van-popup>

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
.me-head {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: calc(var(--safe-top) + 28px) 20px 28px;
  background: linear-gradient(180deg, var(--c-accent), #ffe680 80%, var(--c-bg));
}
.avatar {
  width: 60px;
  height: 60px;
  border-radius: 18px;
  background: #1f1f1f;
  color: var(--c-accent);
  display: grid;
  place-items: center;
  font-size: 24px;
  font-weight: 800;
  flex: none;
}
.avatar.guest {
  background: rgba(255, 255, 255, 0.7);
  color: var(--c-text-2);
}
.who {
  min-width: 0;
}
.name {
  font-size: 19px;
  font-weight: 800;
  overflow-wrap: anywhere;
}
.email {
  font-size: 13px;
  color: var(--c-text-2);
  overflow-wrap: anywhere;
}
.auth-actions {
  display: flex;
  gap: 10px;
}
.card {
  overflow: hidden;
}
.seg {
  display: inline-flex;
  background: #f2f2f2;
  border-radius: 999px;
  padding: 2px;
}
.seg button {
  min-height: 36px;
  min-width: 64px;
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
.tips {
  padding: 12px 16px;
}
.tips-title {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}
.tips p {
  margin: 6px 0 0;
  font-size: 13px;
}
.edit {
  margin-left: auto;
  width: 44px;
  height: 44px;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.6);
  cursor: pointer;
  flex: none;
}
.secure {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
}
.secure p {
  margin: 2px 0 0;
  font-size: 12px;
}
.secure > div {
  flex: 1;
  min-width: 0;
}
.sheet {
  padding: 20px 16px 16px;
}
.sheet h2 {
  font-size: 18px;
  margin: 0 0 10px;
}
.lang-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px 16px;
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

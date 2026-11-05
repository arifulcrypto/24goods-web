<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { setLocale } from '../locale'
import type { AppLocale } from '../i18n'
import { confirm, showToast, toastError } from '../ui'

const { t, locale } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const convs = useConversationsStore()
const initial = computed(() => (auth.user?.display_name || auth.user?.email || '?').charAt(0).toUpperCase())

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
</script>

<template>
  <div class="page with-tabbar">
    <header class="me-head">
      <template v-if="auth.user">
        <span class="avatar" aria-hidden="true">{{ initial }}</span>
        <div class="who">
          <div class="name">{{ auth.user.display_name }}</div>
          <div class="email">{{ auth.user.email }}</div>
        </div>
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

    <section class="section card">
      <van-cell-group :border="false">
        <van-cell v-if="auth.user" :title="t('me.orders')" icon="orders-o" is-link to="/orders" />
        <van-cell v-if="auth.user" :title="t('me.messages')" icon="chat-o" is-link to="/messages" :value="convs.totalUnread ? String(convs.totalUnread) : ''" />
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
      </van-cell-group>
    </section>

    <section class="section card tips">
      <div class="tips-title"><van-icon name="shield-o" /> {{ t('me.safety') }}</div>
      <p class="muted">{{ t('me.safetyText') }}</p>
    </section>

    <div v-if="auth.user" class="section">
      <van-button block round plain type="danger" @click="logout">{{ t('me.logout') }}</van-button>
    </div>
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
</style>

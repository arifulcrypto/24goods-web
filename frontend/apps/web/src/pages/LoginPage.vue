<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { isApiError, safeRedirect } from '@24goods/shared'
import { api } from '../api'
import AuthShell from '../components/AuthShell.vue'
import PowStatus from '../components/PowStatus.vue'
import TurnstileBox from '../components/TurnstileBox.vue'
import { useTurnstile } from '../composables/useTurnstile'
import { useSiteStore } from '../stores/site'
import { isPowAbort, usePow } from '../composables/usePow'
import { useAuthStore } from '../stores/auth'
import { showFailToast, showSuccessToast, toastError } from '../ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const pow = usePow()
const ts = useTurnstile('login')
const site = useSiteStore()
const email = ref('')
const password = ref('')
const show = ref(false)
const busy = ref(false)
/** v5 §5.3: full-page redirect to Google; `next` is a relative path only. */
const agree = ref(false)
const legal = computed(() => site.legal)
const googleUrl = computed(() =>
  api.auth.googleStartUrl(safeRedirect(route.query.redirect, '/'), !legal.value || agree.value),
)
/** The Google callback redirects here with ?error=<code> (query is data, so only known codes are shown). */
const oauthError = computed(() => {
  const e = route.query.error
  return e === 'terms_required' || e === 'oauth_failed' || e === 'feature_disabled' ? `errors.${e}` : null
})

async function submit() {
  if (busy.value) return
  busy.value = true
  try {
    const token = ts.require()
    try {
      await auth.login(email.value.trim(), password.value, undefined, token)
    } catch (e) {
      // after repeated failures the server asks for a proof-of-work (review-v3 §3)
      if (!(isApiError(e) && e.code === 'pow_required')) throw e
      await auth.login(email.value.trim(), password.value, await pow.run('login'), token)
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
    ts.reset()
  }
}
</script>

<template>
  <AuthShell :title="t('auth.loginTitle')" :subtitle="t('auth.loginSubtitle')">
    <div v-if="oauthError" class="oauth-err" role="alert">{{ t(oauthError) }}</div>
    <van-form @submit="submit">
      <van-field
        v-model="email"
        name="email"
        type="text"
        autocomplete="username"
        autocapitalize="none"
        :label="t('auth.loginId')"
        :placeholder="t('auth.loginIdPlaceholder')"
        :rules="[{ validator: (v: string) => v.trim().length > 0, message: t('common.required') }]"
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
      <TurnstileBox :ts="ts" class="ts" />
      <div class="submit">
        <PowStatus :solving="pow.solving.value" :progress="pow.progress.value" />
        <van-button round block type="primary" native-type="submit" :loading="busy">{{ t('me.login') }}</van-button>
      </div>
    </van-form>
    <div class="extras">
      <RouterLink :to="{ name: 'forgot' }" class="link-sm">{{ t('auth.forgot') }}</RouterLink>
    </div>
    <div v-if="site.googleLogin" class="submit">
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
      <van-button round block plain :url="googleUrl" :disabled="!!legal && !agree" class="g-btn">{{ t('auth.google') }}</van-button>
    </div>
    <template #foot>
      <template v-if="site.features.registration">
        <span class="muted">{{ t('auth.noAccount') }}</span>
        <RouterLink :to="{ name: 'register', query: route.query }" class="link">{{ t('me.register') }}</RouterLink>
      </template>
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
.extras {
  text-align: end;
  padding: 0 16px;
}
.link-sm {
  display: inline-block;
  padding: 12px 4px;
  font-size: 13px;
  text-decoration: none;
}
.oauth-err {
  margin: 12px 16px 0;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--van-danger-color);
  background: color-mix(in srgb, var(--van-danger-color) 10%, transparent);
}
.terms {
  padding-bottom: 12px;
  font-size: 13px;
}
.tlink {
  text-decoration: underline;
}
.g-btn {
  min-height: 44px;
}
</style>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { computed } from 'vue'
import { goBack, safeAssetUrl, storeInitial } from '@24goods/shared'
import { useSiteStore } from '../stores/site'
import { LogoMark } from '@24goods/shared/ui'
defineProps<{ title: string; subtitle: string }>()
const { t } = useI18n()
const router = useRouter()
const site = useSiteStore()
const logo = computed(() => (site.whiteLabel ? safeAssetUrl(site.storefront?.logo_url) : null))
function back() {
  goBack(router, '/')
}
</script>

<template>
  <div class="auth">
    <button type="button" class="back" :aria-label="t('common.back')" @click="back"><van-icon name="arrow-left" size="20" /></button>
    <div class="brand">
      <img v-if="logo" :src="logo" class="mono" width="48" height="48" alt="" />
      <span v-else-if="site.whiteLabel" class="mono" aria-hidden="true">{{ storeInitial(site.storefront?.store_name) }}</span>
      <LogoMark v-else :size="48" />
    </div>
    <h1>{{ title }}</h1>
    <p class="muted">{{ subtitle }}</p>
    <div class="auth-card card"><slot /></div>
    <div class="foot"><slot name="foot" /></div>
  </div>
</template>

<style scoped>
.auth {
  min-height: 100vh;
  min-height: 100dvh;
  padding: calc(var(--safe-top) + 8px) 16px calc(var(--safe-bottom) + 24px);
  background: linear-gradient(180deg, var(--c-accent) 0, #ffe680 180px, var(--c-bg) 320px);
}
.back {
  width: 44px;
  height: 44px;
  border: 0;
  background: none;
  display: grid;
  place-items: center;
  color: var(--c-text);
  cursor: pointer;
  margin-left: -8px;
}
.brand {
  margin: 8px 0 12px;
}
.mono {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  object-fit: cover;
  background: #1f1f1f;
  color: var(--c-accent);
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 22px;
}
.logo {
  width: 52px;
  height: 52px;
  border-radius: 16px;
  background: #1f1f1f;
  color: var(--c-accent);
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 22px;
}
h1 {
  margin: 0;
  font-size: 24px;
}
p {
  margin: 4px 0 20px;
}
.auth-card {
  padding: 8px 0 16px;
  overflow: hidden;
}
.foot {
  text-align: center;
  margin-top: 20px;
}
</style>

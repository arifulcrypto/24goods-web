<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { goBack } from '@24goods/shared'
import { LogoMark } from '@24goods/shared/ui'
defineProps<{ title: string; subtitle: string }>()
const { t } = useI18n()
const router = useRouter()
function back() {
  goBack(router, '/')
}
</script>

<template>
  <div class="auth">
    <button type="button" class="back" :aria-label="t('common.back')" @click="back"><van-icon name="arrow-left" size="20" /></button>
    <div class="brand"><LogoMark :size="48" /></div>
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

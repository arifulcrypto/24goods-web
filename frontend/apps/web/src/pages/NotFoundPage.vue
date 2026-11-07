<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useNoindex } from '../composables/useNoindex'

const { t } = useI18n()
const router = useRouter()
const q = ref('')
useNoindex()

function submit() {
  const v = q.value.trim().slice(0, 100)
  void router.push({ path: '/search', query: v ? { q: v } : {} })
}
</script>

<template>
  <main class="nf">
    <p class="code" aria-hidden="true">404</p>
    <h1 class="title">{{ t('notFound.title') }}</h1>
    <p class="text">{{ t('notFound.text') }}</p>
    <form class="form" role="search" @submit.prevent="submit">
      <van-search v-model="q" shape="round" maxlength="100" :aria-label="t('notFound.searchPlaceholder')" :placeholder="t('notFound.searchPlaceholder')" @search="submit" />
    </form>
    <div class="actions">
      <van-button round type="primary" to="/">{{ t('notFound.home') }}</van-button>
      <van-button round plain to="/help">{{ t('notFound.help') }}</van-button>
    </div>
  </main>
</template>

<style scoped>
.nf {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: calc(24px + env(safe-area-inset-top)) calc(16px + env(safe-area-inset-right)) calc(24px + env(safe-area-inset-bottom)) calc(16px + env(safe-area-inset-left));
  text-align: center;
}
.code {
  margin: 0;
  font-size: 72px;
  font-weight: 800;
  line-height: 1;
  letter-spacing: 2px;
  color: var(--van-text-color, #323233);
}
.title {
  margin: 4px 0 0;
  font-size: 20px;
}
.text {
  margin: 0;
  max-width: 320px;
  color: var(--van-text-color-2, #646566);
  font-size: 14px;
  line-height: 1.5;
}
.form {
  width: 100%;
  max-width: 360px;
  margin-top: 8px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
}
.actions .van-button {
  min-width: 128px;
  min-height: 44px;
}
</style>

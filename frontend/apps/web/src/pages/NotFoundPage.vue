<script setup lang="ts">
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
</script>

<template>
  <div class="nf">
    <van-empty image="error" :description="t('notFound.title')">
      <van-button round type="primary" to="/">{{ t('notFound.home') }}</van-button>
    </van-empty>
  </div>
</template>

<style scoped>
.nf {
  min-height: 100vh;
  display: grid;
  place-items: center;
}
</style>

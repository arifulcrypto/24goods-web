<script setup lang="ts">
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
</script>

<template>
  <el-result icon="info" :title="t('notFound.title')">
    <template #extra>
      <RouterLink to="/"><el-button type="primary">{{ t('forbidden.back') }}</el-button></RouterLink>
    </template>
  </el-result>
</template>

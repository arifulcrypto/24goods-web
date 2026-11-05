<script setup lang="ts">
import { elementLocale } from './locale'
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <RouterView />
  </el-config-provider>
</template>

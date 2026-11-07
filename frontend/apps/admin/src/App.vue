<script setup lang="ts">
import { accessBlock } from '@24goods/shared'
import { AccessBlockedView } from '@24goods/shared/ui'
import { elementLocale } from './locale'
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <RouterView />
    <AccessBlockedView v-if="accessBlock" :block="accessBlock" />
  </el-config-provider>
</template>

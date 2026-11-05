<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { OrderStatus } from '@24goods/shared'

const props = defineProps<{ status: OrderStatus }>()
const { t } = useI18n()
const type = computed(() => {
  switch (props.status) {
    case 'pending_payment':
      return 'warning'
    case 'paid':
      return 'primary'
    case 'delivered':
    case 'completed':
      return 'success'
    case 'manual_review':
      return 'danger'
    default:
      return 'info'
  }
})
</script>

<template>
  <el-tag :type="type" size="small" effect="light" disable-transitions>{{ t(`orderStatus.${status}`) }}</el-tag>
</template>

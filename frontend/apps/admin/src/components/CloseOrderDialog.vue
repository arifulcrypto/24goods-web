<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Order } from '@24goods/shared'
import { apiV4 } from '../api'
import { toastError, toastOk } from '../ui'

const props = defineProps<{ modelValue: boolean; orderId: string | null; orderNo?: string; checkoutNo?: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; closed: [o: Order] }>()
const { t } = useI18n()
const reason = ref('')
const error = ref('')
const busy = ref(false)
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      reason.value = ''
      error.value = ''
    }
  },
)
async function submit() {
  if (busy.value || !props.orderId) return
  if (!reason.value.trim()) {
    error.value = t('orders.reasonRequired')
    return
  }
  busy.value = true
  try {
    const o = await apiV4.orders.close(props.orderId, reason.value.trim())
    toastOk(t('orders.closed'))
    emit('closed', o)
    emit('update:modelValue', false)
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog :model-value="modelValue" :title="t('orders.close')" width="min(460px, 94vw)" append-to-body @update:model-value="emit('update:modelValue', $event)">
    <el-alert type="warning" :closable="false" show-icon class="gap" :title="t('orders.closeWarn')" />
    <p v-if="checkoutNo" class="muted small">{{ t('orders.closeCheckout', { no: checkoutNo }) }}</p>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item :label="t('orders.closeReason')" :error="error" required>
        <el-input v-model="reason" maxlength="200" show-word-limit :placeholder="t('orders.closeReasonPlaceholder')" />
        <div class="muted small">{{ t('orders.closeReasonHint') }}</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('common.close') }}</el-button>
      <el-button type="danger" :loading="busy" @click="submit">{{ t('orders.close') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.gap {
  margin-bottom: 10px;
}
.small {
  font-size: 12px;
}
</style>

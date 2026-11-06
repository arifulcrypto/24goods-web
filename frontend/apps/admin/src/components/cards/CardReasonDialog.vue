<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

/** Shared "reason required" dialog for void actions. Emits the trimmed reason. */
const props = defineProps<{ modelValue: boolean; title: string; message: string; busy?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; confirm: [reason: string] }>()
const { t } = useI18n()
const reason = ref('')
const error = ref('')
watch(
  () => props.modelValue,
  (o) => {
    if (o) {
      reason.value = ''
      error.value = ''
    }
  },
)
function ok() {
  if (!reason.value.trim()) {
    error.value = t('orders.reasonRequired')
    return
  }
  emit('confirm', reason.value.trim())
}
</script>

<template>
  <el-dialog :model-value="modelValue" :title="title" width="min(460px, 94vw)" append-to-body @update:model-value="emit('update:modelValue', $event)">
    <p class="msg">{{ message }}</p>
    <el-form label-position="top" @submit.prevent="ok">
      <el-form-item :label="t('orders.reason')" :error="error">
        <el-input v-model="reason" maxlength="200" show-word-limit autofocus />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('common.cancel') }}</el-button>
      <el-button type="danger" :loading="busy" @click="ok">{{ t('cards.void') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.msg {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.5;
}
</style>

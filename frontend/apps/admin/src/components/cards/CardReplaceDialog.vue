<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV3 } from '../../api'
import { toastError, toastOk } from '../../ui'

const props = defineProps<{ modelValue: boolean; cardId: string | null; masked: string }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; done: [] }>()
const { t } = useI18n()
const code = ref('')
const busy = ref(false)
const error = ref('')
watch(
  () => props.modelValue,
  (o) => {
    if (o) {
      code.value = ''
      error.value = ''
    }
  },
)
async function run() {
  if (!props.cardId || busy.value) return
  if (!code.value.trim()) {
    error.value = t('cards.replace.required')
    return
  }
  busy.value = true
  try {
    await apiV3.cards.replace(props.cardId, code.value.trim())
    code.value = ''
    toastOk(t('cards.replace.done'))
    emit('done')
    emit('update:modelValue', false)
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog :model-value="modelValue" :title="t('cards.replace.title')" width="min(520px, 94vw)" append-to-body @update:model-value="emit('update:modelValue', $event)">
    <p class="msg">{{ t('cards.replace.explain', { masked }) }}</p>
    <el-form label-position="top" @submit.prevent="run">
      <el-form-item :label="t('cards.replace.newContent')" :error="error">
        <el-input v-model="code" type="textarea" :rows="3" spellcheck="false" autocomplete="off" class="mono" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" @click="run">{{ t('cards.replace.go') }}</el-button>
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

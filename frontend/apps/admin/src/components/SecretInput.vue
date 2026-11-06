<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { secretLabel, type SecretField } from '../lib/secret'

/** Write-only secret: never prefilled; shows "Configured ••1234" with Replace / Clear. */
const field = defineModel<SecretField>({ required: true })
const props = defineProps<{ label: string; placeholder?: string }>()
const { t } = useI18n()
const label = computed(() => secretLabel(field.value, t('v5.secret.configured')))
const editing = computed(() => field.value.mode === 'replace' || !field.value.configured)
function replace() {
  field.value = { ...field.value, mode: 'replace', value: '' }
}
function clear() {
  field.value = { ...field.value, mode: 'clear', value: '' }
}
function undo() {
  field.value = { ...field.value, mode: 'idle', value: '' }
}
</script>

<template>
  <el-form-item :label="props.label">
    <div class="row">
      <template v-if="field.mode === 'clear'">
        <el-tag type="danger" effect="plain">{{ t('v5.secret.willClear') }}</el-tag>
        <el-button link type="primary" @click="undo">{{ t('v5.secret.undo') }}</el-button>
      </template>
      <template v-else-if="editing">
        <el-input
          v-model="field.value"
          type="password"
          autocomplete="new-password"
          show-password
          :placeholder="props.placeholder ?? t('v5.secret.enter')"
          class="inp"
        />
        <el-button v-if="field.configured" link type="primary" @click="undo">{{ t('common.cancel') }}</el-button>
      </template>
      <template v-else>
        <el-tag type="success" effect="plain">{{ label }}</el-tag>
        <el-button link type="primary" @click="replace">{{ t('v5.secret.replace') }}</el-button>
        <el-button link type="danger" @click="clear">{{ t('v5.secret.clear') }}</el-button>
      </template>
      <el-tag v-if="!field.configured && field.mode !== 'replace'" type="info" effect="plain">{{ t('v5.secret.notSet') }}</el-tag>
    </div>
    <div class="muted small">{{ t('v5.secret.hint') }}</div>
  </el-form-item>
</template>

<style scoped>
.row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
}
.inp {
  max-width: 360px;
}
.small {
  font-size: 12px;
  width: 100%;
}
</style>

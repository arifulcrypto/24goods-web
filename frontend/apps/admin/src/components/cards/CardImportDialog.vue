<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AdminVariant } from '@24goods/shared'
import { api, apiV3 } from '../../api'
import type { CardImportResultV3 } from '../../api-v3'
import { IMPORT_MAX_LINES, countLines } from '../../lib/cards'
import { confirmBox, toastError } from '../../ui'

const props = defineProps<{ modelValue: boolean; productId: string; productTitle: string }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; imported: [] }>()
const { t, locale } = useI18n()
const lines = ref('')
const note = ref('')
const expiry = ref('')
const busy = ref(false)
const result = ref<CardImportResultV3 | null>(null)
const n = computed(() => countLines(lines.value))
const tooMany = computed(() => n.value > IMPORT_MAX_LINES)
/** v11 W1: a product with active variants takes cards per variant (required). */
const variants = ref<AdminVariant[]>([])
const variantId = ref('')
const activeVariants = computed(() => variants.value.filter((v) => v.status === 'active'))
const needsVariant = computed(() => activeVariants.value.length > 0)
async function loadVariants() {
  variants.value = []
  variantId.value = ''
  try {
    variants.value = (await api.products.variants.list(props.productId)).items
    if (activeVariants.value.length === 1) variantId.value = activeVariants.value[0]!.id
  } catch (e) {
    toastError(e)
  }
}
const variantName = (v: AdminVariant) => (locale.value === 'zh-CN' && v.name_i18n['zh-CN']) || v.name_i18n.en

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      lines.value = ''
      note.value = ''
      expiry.value = ''
      result.value = null
      void loadVariants()
    }
  },
)

async function run() {
  if (busy.value || !n.value || tooMany.value || (needsVariant.value && !variantId.value)) return
  if (!(await confirmBox(t('inventory.confirmImport', { n: n.value, title: props.productTitle })))) return
  busy.value = true
  try {
    result.value = await apiV3.products.importCards(props.productId, {
      lines: lines.value,
      note: note.value.trim() || undefined,
      expires_at: expiry.value || undefined,
      ...(variantId.value ? { variant_id: variantId.value } : {}),
    })
    // never keep plaintext around once it is sealed server-side
    lines.value = ''
    emit('imported')
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog :model-value="modelValue" :title="t('inventory.importTitle')" width="min(680px, 96vw)" destroy-on-close @update:model-value="emit('update:modelValue', $event)">
    <el-select
      v-if="needsVariant"
      v-model="variantId"
      class="variant-pick"
      :placeholder="t('variants.importPick')"
      :aria-label="t('variants.importPick')"
    >
      <el-option v-for="v in activeVariants" :key="v.id" :value="v.id" :label="variantName(v)" />
    </el-select>
    <el-input
      v-model="lines"
      type="textarea"
      :rows="9"
      :placeholder="t('inventory.placeholder')"
      spellcheck="false"
      autocomplete="off"
      class="mono"
      :aria-label="t('inventory.importTitle')"
    />
    <div class="muted small hint">{{ t('cards.import.expHint') }}</div>
    <div class="row">
      <el-input v-model="note" :placeholder="t('cards.import.note')" maxlength="200" :aria-label="t('cards.import.note')" />
      <el-date-picker v-model="expiry" type="date" value-format="YYYY-MM-DD" :placeholder="t('cards.import.expiry')" :aria-label="t('cards.import.expiry')" />
    </div>
    <div :class="tooMany ? 'err' : 'muted'" class="small">
      {{ t('inventory.lines', n) }}<template v-if="tooMany"> — {{ t('inventory.tooMany') }}</template>
    </div>
    <el-alert v-if="result" type="success" :title="t('inventory.resultTitle')" show-icon :closable="false" class="result">
      <div class="stats">
        <el-statistic :title="t('inventory.imported')" :value="result.imported" />
        <el-statistic :title="t('inventory.duplicates')" :value="result.duplicates" />
        <el-statistic :title="t('inventory.rejected')" :value="result.rejected" />
      </div>
      <p v-if="result.batch_id" class="small">{{ t('cards.import.batch') }}: <span class="mono">{{ result.batch_id.slice(0, 8) }}</span></p>
      <p class="small muted">{{ t('inventory.cleared') }}</p>
    </el-alert>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('common.close') }}</el-button>
      <el-button type="primary" :disabled="!n || tooMany || (needsVariant && !variantId)" :loading="busy" @click="run">{{ t('inventory.import') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.variant-pick {
  width: 100%;
  margin-bottom: 10px;
}
.row {
  display: flex;
  gap: 12px;
  margin: 10px 0 6px;
  flex-wrap: wrap;
}
.row > * {
  flex: 1 1 220px;
}
.small {
  font-size: 12px;
}
.hint {
  margin-top: 6px;
}
.err {
  color: var(--el-color-danger);
}
.result {
  margin-top: 12px;
}
.stats {
  display: flex;
  gap: 32px;
}
.result p {
  margin: 4px 0 0;
}
</style>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Search } from '@element-plus/icons-vue'
import { formatMoney } from '@24goods/shared'
import { apiV3 } from '../api'
import type { AdminProductV3 } from '../api-v3'
import { toastError } from '../ui'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; pick: [productId: string] }>()
const { t, locale } = useI18n()
const q = ref('')
const items = ref<AdminProductV3[]>([])
const loading = ref(false)
let deb: ReturnType<typeof setTimeout> | null = null

async function load() {
  loading.value = true
  try {
    const r = await apiV3.products.list({ q: q.value.trim() || undefined, status: 'active', page: 1, page_size: 20 })
    items.value = r.items
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
}
watch(
  () => props.modelValue,
  (o) => {
    if (o) {
      q.value = ''
      void load()
    }
  },
)
watch(q, () => {
  if (deb) clearTimeout(deb)
  deb = setTimeout(() => void load(), 300)
})
const titleOf = (p: AdminProductV3) => (locale.value === 'zh-CN' && p.title_i18n['zh-CN']) || p.title_i18n.en
</script>

<template>
  <el-dialog :model-value="modelValue" :title="t('inbox.pickProduct')" width="min(560px, 96vw)" append-to-body @update:model-value="emit('update:modelValue', $event)">
    <el-input v-model="q" :placeholder="t('inbox.pickSearch')" :prefix-icon="Search" clearable />
    <div v-loading="loading" class="list">
      <div v-if="!items.length && !loading" class="muted empty">{{ t('inbox.pickEmpty') }}</div>
      <div v-for="p in items" :key="p.id" class="row">
        <div class="main">
          <div class="title">{{ titleOf(p) }}</div>
          <div class="muted small">
            <span class="price">{{ formatMoney(p.price, p.currency, locale) }}</span>
            · {{ t('products.col.stock') }}: {{ p.stock ?? '—' }}
          </div>
        </div>
        <el-button type="primary" size="small" @click="emit('pick', p.id)">{{ t('inbox.pickSend') }}</el-button>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.list {
  margin-top: 10px;
  max-height: 50vh;
  overflow: auto;
  min-height: 80px;
}
.row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--a-line);
}
.main {
  flex: 1;
  min-width: 0;
}
.title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.small {
  font-size: 12px;
}
.empty {
  padding: 16px;
  text-align: center;
}
</style>

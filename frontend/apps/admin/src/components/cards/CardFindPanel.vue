<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Search } from '@element-plus/icons-vue'
import { formatDateTime } from '@24goods/shared'
import { apiV3 } from '../../api'
import type { CardRowV3, FindBy } from '../../api-v3'
import { FIND_MAX_LINES, cleanLines, daysUntil } from '../../lib/cards'
import { toastError } from '../../ui'

/**
 * "Find card" (review-v3 §8). Pasted codes live only in this component's memory: they go to the
 * server in a POST body, never into a URL, log or localStorage, and the textarea is cleared
 * right after the search.
 */
const { t, locale } = useI18n()
const text = ref('')
const by = ref<FindBy>('content')
const busy = ref(false)
const searched = ref(false)
const items = ref<CardRowV3[]>([])
const count = computed(() => cleanLines(text.value).length)
const tooMany = computed(() => count.value > FIND_MAX_LINES)

async function find() {
  if (busy.value || !count.value || tooMany.value) return
  const lines = cleanLines(text.value)
  busy.value = true
  try {
    items.value = (await apiV3.cards.find(lines, by.value)).items
    searched.value = true
    text.value = ''
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
function orderLink(r: CardRowV3) {
  return r.order_id ? { path: '/orders', query: { open: r.order_id } } : { path: '/orders', query: { q: r.order_no ?? undefined } }
}
</script>

<template>
  <section class="panel find" :aria-label="t('cards.find.title')">
    <h2 class="h2">{{ t('cards.find.title') }}</h2>
    <p class="muted small">{{ t('cards.find.privacy') }}</p>
    <el-input
      v-model="text"
      type="textarea"
      :rows="4"
      :placeholder="t('cards.find.placeholder')"
      spellcheck="false"
      autocomplete="off"
      class="mono"
      :aria-label="t('cards.find.title')"
    />
    <div class="bar">
      <el-radio-group v-model="by" size="small">
        <el-radio-button value="content">{{ t('cards.find.byContent') }}</el-radio-button>
        <el-radio-button value="first_field">{{ t('cards.find.byFirst') }}</el-radio-button>
      </el-radio-group>
      <span :class="tooMany ? 'err' : 'muted'" class="small">
        {{ t('inventory.lines', count) }}<template v-if="tooMany"> — {{ t('cards.find.tooMany', { n: FIND_MAX_LINES }) }}</template>
      </span>
      <el-button type="primary" :icon="Search" :disabled="!count || tooMany" :loading="busy" @click="find">{{ t('cards.find.go') }}</el-button>
    </div>
    <template v-if="searched">
      <el-table :data="items" :empty-text="t('cards.find.none')" class="res" size="small">
        <el-table-column :label="t('inventory.masked')" min-width="190">
          <template #default="{ row }"><span class="mono">{{ row.masked }}</span></template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'available' ? 'success' : row.status === 'void' ? 'danger' : 'info'">{{ t(`inventory.cardStatus.${row.status}`) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('cards.col.product')" prop="product_title" min-width="160" show-overflow-tooltip />
        <el-table-column :label="t('cards.col.batch')" width="110">
          <template #default="{ row }"><span class="mono small">{{ row.batch_id ? row.batch_id.slice(0, 8) : '—' }}</span></template>
        </el-table-column>
        <el-table-column :label="t('cards.col.expires')" width="150">
          <template #default="{ row }">
            <template v-if="row.expires_at">{{ formatDateTime(row.expires_at, locale) }}<span v-if="(daysUntil((row as CardRowV3).expires_at) ?? 99) <= 7" class="warn"> · {{ t('cards.expiringIn', { n: Math.max(0, daysUntil(row.expires_at) ?? 0) }) }}</span></template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('inventory.order')" min-width="150">
          <template #default="{ row }">
            <RouterLink v-if="row.order_no" :to="orderLink(row as CardRowV3)" class="mono small">{{ row.order_no }}</RouterLink>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
      <p class="muted small">{{ t('cards.find.cleared') }}</p>
    </template>
  </section>
</template>

<style scoped>
.find {
  margin-bottom: 16px;
  border-left: 3px solid var(--a-accent, #ff6a2b);
}
.h2 {
  font-size: 16px;
  margin: 0 0 4px;
}
.small {
  font-size: 12px;
}
.bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}
.res {
  margin-top: 12px;
}
.err {
  color: var(--el-color-danger);
}
.warn {
  color: var(--el-color-warning);
}
</style>

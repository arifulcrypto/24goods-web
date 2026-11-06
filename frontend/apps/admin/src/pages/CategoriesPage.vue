<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus } from '@element-plus/icons-vue'
import { isApiError } from '@24goods/shared'
import { apiV5 } from '../api'
import type { AdminCategory } from '../api-v5'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const items = ref<AdminCategory[]>([])
const loading = ref(false)
const dialog = ref(false)
const saving = ref(false)
const editing = ref<AdminCategory | null>(null)
const errors = ref<Record<string, string>>({})
const f = reactive({ slug: '', name_en: '', name_zh: '', icon: '', sort: 0, enabled: true })
const SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/

async function load() {
  loading.value = true
  try {
    items.value = [...(await apiV5.categories.list())].sort((a, b) => a.sort - b.sort)
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

function openNew() {
  editing.value = null
  errors.value = {}
  Object.assign(f, { slug: '', name_en: '', name_zh: '', icon: '', sort: (items.value.at(-1)?.sort ?? 0) + 10, enabled: true })
  dialog.value = true
}
function openEdit(c: AdminCategory) {
  editing.value = c
  errors.value = {}
  Object.assign(f, { slug: c.slug, name_en: c.name_i18n.en, name_zh: c.name_i18n['zh-CN'], icon: c.icon ?? '', sort: c.sort, enabled: c.enabled })
  dialog.value = true
}
async function save() {
  if (saving.value) return
  const e: Record<string, string> = {}
  if (!f.name_en.trim()) e.name = t('v5.categories.nameRequired')
  if (!editing.value && !SLUG_RE.test(f.slug.trim())) e.slug = t('v5.categories.slugInvalid')
  errors.value = e
  if (Object.keys(e).length) return
  saving.value = true
  const body = {
    name_i18n: { en: f.name_en.trim(), 'zh-CN': f.name_zh.trim() || f.name_en.trim() },
    icon: f.icon.trim() || null,
    sort: f.sort,
    enabled: f.enabled,
  }
  try {
    if (editing.value) await apiV5.categories.update(editing.value.id, body)
    else await apiV5.categories.create({ ...body, slug: f.slug.trim() })
    toastOk(t('v5.categories.saved'))
    dialog.value = false
    await load()
  } catch (err) {
    if (isApiError(err) && err.status === 409) errors.value = { slug: t('v5.categories.slugTaken') }
    else toastError(err)
  } finally {
    saving.value = false
  }
}
async function toggle(c: AdminCategory, v: boolean) {
  try {
    await apiV5.categories.update(c.id, { enabled: v })
    c.enabled = v
  } catch (e) {
    toastError(e)
  }
}
async function remove(c: AdminCategory) {
  if (!(await confirmBox(t('v5.categories.deleteConfirm', { name: nameOf(c) })))) return
  try {
    await apiV5.categories.remove(c.id)
    toastOk(t('v5.categories.deleted'))
    await load()
  } catch (e) {
    if (isApiError(e) && e.code === 'category_in_use') {
      if (await confirmBox(t('v5.categories.inUse', { name: nameOf(c) }), t('v5.categories.inUseTitle'))) await toggle(c, false)
    } else toastError(e)
  }
}
const nameOf = (c: AdminCategory) => (locale.value === 'zh-CN' && c.name_i18n['zh-CN']) || c.name_i18n.en
</script>

<template>
  <div class="page">
    <div class="page-head">
      <span class="muted">{{ t('v5.categories.note') }}</span>
      <el-button type="primary" :icon="Plus" @click="openNew">{{ t('v5.categories.new') }}</el-button>
    </div>
    <div class="panel">
      <el-table v-loading="loading" :data="items" row-key="id" :empty-text="t('common.empty')">
        <el-table-column :label="t('v5.categories.name')" min-width="200">
          <template #default="{ row }">
            <div>{{ row.name_i18n.en }}</div>
            <div class="muted small">{{ row.name_i18n['zh-CN'] }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('v5.categories.slug')" width="160"><template #default="{ row }"><span class="mono">{{ row.slug }}</span></template></el-table-column>
        <el-table-column :label="t('v5.categories.icon')" width="120"><template #default="{ row }">{{ row.icon ?? '—' }}</template></el-table-column>
        <el-table-column :label="t('v5.categories.sort')" prop="sort" width="90" />
        <el-table-column :label="t('v5.categories.products')" width="100"><template #default="{ row }">{{ row.product_count ?? '—' }}</template></el-table-column>
        <el-table-column :label="t('v5.enabled')" width="100">
          <template #default="{ row }"><el-switch :model-value="row.enabled" :aria-label="t('v5.enabled')" @change="(v: string | number | boolean) => toggle(row as AdminCategory, !!v)" /></template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row as AdminCategory)">{{ t('common.edit') }}</el-button>
            <el-button link type="danger" @click="remove(row as AdminCategory)">{{ t('v5.delete') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dialog" :title="editing ? t('v5.categories.edit') : t('v5.categories.new')" width="min(480px, 96vw)" destroy-on-close>
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item :label="t('v5.categories.slug')" :error="errors.slug">
          <el-input v-model="f.slug" :disabled="!!editing" maxlength="32" spellcheck="false" class="mono" />
          <div class="muted small">{{ t('v5.categories.slugHint') }}</div>
        </el-form-item>
        <el-form-item :label="`${t('v5.categories.name')} (EN)`" :error="errors.name"><el-input v-model="f.name_en" maxlength="40" /></el-form-item>
        <el-form-item :label="`${t('v5.categories.name')} (中文)`"><el-input v-model="f.name_zh" maxlength="40" /></el-form-item>
        <el-form-item :label="t('v5.categories.icon')"><el-input v-model="f.icon" maxlength="40" :placeholder="t('v5.categories.iconHint')" /></el-form-item>
        <el-form-item :label="t('v5.categories.sort')"><el-input-number v-model="f.sort" :min="0" :max="10000" /></el-form-item>
        <el-form-item :label="t('v5.enabled')"><el-switch v-model="f.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.small {
  font-size: 12px;
}
</style>

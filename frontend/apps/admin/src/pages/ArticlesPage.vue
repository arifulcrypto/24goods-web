<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus, Search } from '@element-plus/icons-vue'
import { formatDateTime } from '@24goods/shared'
import { apiV3 } from '../api'
import type { AdminArticle, ArticleKind, ArticleStatus } from '../api-v3'
import { usePaged } from '../composables/usePaged'
import { FIXED_SLUGS, isValidSlug, slugify } from '../lib/content'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const q = ref('')
const status = ref<ArticleStatus | ''>('')
const kind = ref<ArticleKind | ''>('')
const table = usePaged<AdminArticle>((page, page_size) => apiV3.articles.list({ q: q.value.trim() || undefined, status: status.value, kind: kind.value, page, page_size }))
let deb: ReturnType<typeof setTimeout> | null = null
watch(q, () => {
  if (deb) clearTimeout(deb)
  deb = setTimeout(() => void table.reload(), 300)
})
watch([status, kind], () => void table.reload())
onMounted(() => void table.load())

const titleOf = (a: AdminArticle) => (locale.value === 'zh-CN' && a.title_i18n['zh-CN']) || a.title_i18n.en

// ---- editor ----
const dlg = ref(false)
const editing = ref<AdminArticle | null>(null)
const tab = ref<'en' | 'zh-CN'>('en')
const saving = ref(false)
const errors = ref<Record<string, string>>({})
const form = reactive({ slug: '', kind: 'article' as ArticleKind, category: 'general', title_en: '', title_zh: '', body_en: '', body_zh: '', status: 'draft' as ArticleStatus, pinned: false })
let slugTouched = false

function openEditor(a: AdminArticle | null) {
  editing.value = a
  tab.value = 'en'
  errors.value = {}
  slugTouched = !!a
  form.slug = a?.slug ?? ''
  form.kind = a?.kind ?? 'article'
  form.category = a?.category ?? 'general'
  form.title_en = a?.title_i18n.en ?? ''
  form.title_zh = a?.title_i18n['zh-CN'] ?? ''
  form.body_en = a?.body_i18n.en ?? ''
  form.body_zh = a?.body_i18n['zh-CN'] ?? ''
  form.status = a?.status ?? 'draft'
  form.pinned = a?.pinned ?? false
  dlg.value = true
}
function onTitle() {
  if (!slugTouched) form.slug = slugify(form.title_en)
}
async function openRow(a: AdminArticle) {
  // the list may omit bodies; fetch the full record for editing
  try {
    openEditor(await apiV3.articles.get(a.id))
  } catch (e) {
    toastError(e)
  }
}
function validate(): boolean {
  const e: Record<string, string> = {}
  if (!form.title_en.trim()) e.title = t('articles.v.titleRequired')
  if (!isValidSlug(form.slug)) e.slug = t('articles.v.slugInvalid')
  if (!form.body_en.trim()) e.body = t('articles.v.bodyRequired')
  errors.value = e
  if (e.title || e.body) tab.value = 'en'
  return !Object.keys(e).length
}
async function save() {
  if (saving.value || !validate()) return
  saving.value = true
  const body = {
    slug: form.slug,
    kind: form.kind,
    category: form.category.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').slice(0, 32) || 'general',
    title_i18n: { en: form.title_en.trim(), 'zh-CN': form.title_zh.trim() },
    body_i18n: { en: form.body_en, 'zh-CN': form.body_zh },
    status: form.status,
    pinned: form.pinned,
  }
  try {
    if (editing.value) await apiV3.articles.update(editing.value.id, body)
    else await apiV3.articles.create(body)
    toastOk(t('articles.saved'))
    dlg.value = false
    void table.load()
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}
async function remove(a: AdminArticle) {
  if (!(await confirmBox(t('articles.deleteConfirm', { title: titleOf(a) })))) return
  try {
    await apiV3.articles.remove(a.id)
    toastOk(t('articles.deleted'))
    void table.load()
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="toolbar">
        <el-input v-model="q" :placeholder="t('articles.search')" :prefix-icon="Search" clearable style="width: 240px" />
        <el-select v-model="status" style="width: 140px" :aria-label="t('common.status')">
          <el-option value="" :label="t('products.allStatus')" />
          <el-option value="published" :label="t('articles.status.published')" />
          <el-option value="draft" :label="t('articles.status.draft')" />
        </el-select>
        <el-select v-model="kind" style="width: 140px" :aria-label="t('articles.kind')">
          <el-option value="" :label="t('articles.allKinds')" />
          <el-option value="article" :label="t('articles.kinds.article')" />
          <el-option value="page" :label="t('articles.kinds.page')" />
        </el-select>
      </div>
      <el-button type="primary" :icon="Plus" @click="openEditor(null)">{{ t('articles.new') }}</el-button>
    </div>
    <el-alert type="info" :closable="false" show-icon class="gap" :title="t('articles.fixedNote', { slugs: FIXED_SLUGS.join(', ') })" />
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" row-key="id" :empty-text="t('common.empty')">
        <el-table-column :label="t('products.col.title')" min-width="240">
          <template #default="{ row }">
            <div class="title">{{ titleOf(row as AdminArticle) }}</div>
            <div class="muted small mono">/{{ row.slug }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('articles.kind')" width="100">
          <template #default="{ row }">{{ t(`articles.kinds.${row.kind}`) }}</template>
        </el-table-column>
        <el-table-column :label="t('products.col.category')" prop="category" width="120" />
        <el-table-column :label="t('common.status')" width="110">
          <template #default="{ row }"><el-tag size="small" :type="row.status === 'published' ? 'success' : 'info'">{{ t(`articles.status.${row.status}`) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('articles.pinned')" width="80">
          <template #default="{ row }">{{ row.pinned ? '📌' : '' }}</template>
        </el-table-column>
        <el-table-column :label="t('articles.views')" prop="views" width="80" />
        <el-table-column :label="t('articles.updated')" width="160">
          <template #default="{ row }">{{ formatDateTime(row.updated_at, locale) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openRow(row as AdminArticle)">{{ t('common.edit') }}</el-button>
            <el-button link type="danger" @click="remove(row as AdminArticle)">{{ t('common.delete') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" />
      </div>
    </div>

    <el-dialog v-model="dlg" :title="editing ? t('articles.edit') : t('articles.new')" width="min(780px, 96vw)" destroy-on-close>
      <el-form label-position="top" @submit.prevent="save">
        <el-tabs v-model="tab">
          <el-tab-pane :label="t('products.form.tabEn')" name="en">
            <el-form-item :label="t('products.form.title')" :error="errors.title" required>
              <el-input v-model="form.title_en" maxlength="150" show-word-limit @input="onTitle" />
            </el-form-item>
            <el-form-item :label="t('articles.body')" :error="errors.body" required>
              <el-input v-model="form.body_en" type="textarea" :rows="12" maxlength="50000" class="mono" />
            </el-form-item>
          </el-tab-pane>
          <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
            <el-form-item :label="t('products.form.title')">
              <el-input v-model="form.title_zh" maxlength="150" show-word-limit />
            </el-form-item>
            <el-form-item :label="t('articles.body')">
              <el-input v-model="form.body_zh" type="textarea" :rows="12" maxlength="50000" class="mono" />
            </el-form-item>
          </el-tab-pane>
        </el-tabs>
        <div class="muted small gap">{{ t('articles.markdownNote') }}</div>
        <div class="grid">
          <el-form-item :label="t('articles.slug')" :error="errors.slug">
            <el-input v-model="form.slug" class="mono" maxlength="80" @input="slugTouched = true" />
          </el-form-item>
          <el-form-item :label="t('products.col.category')">
            <el-input v-model="form.category" maxlength="32" />
          </el-form-item>
          <el-form-item :label="t('articles.kind')">
            <el-radio-group v-model="form.kind">
              <el-radio-button value="article">{{ t('articles.kinds.article') }}</el-radio-button>
              <el-radio-button value="page">{{ t('articles.kinds.page') }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="t('common.status')">
            <el-radio-group v-model="form.status">
              <el-radio-button value="draft">{{ t('articles.status.draft') }}</el-radio-button>
              <el-radio-button value="published">{{ t('articles.status.published') }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="t('articles.pinned')">
            <el-switch v-model="form.pinned" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.close') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.title {
  font-weight: 500;
}
.small {
  font-size: 12px;
}
.gap {
  margin-bottom: 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  column-gap: 16px;
}
</style>

<script setup lang="ts">
/**
 * Announcements (v11 W3). Everyone on staff sees the published list with their own unread state
 * and "Mark all as read"; admins also manage them (create / edit / publish / pin / delete),
 * see per-item read counts and the two store toggles.
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus } from '@element-plus/icons-vue'
import { formatDateTime, type AnnouncementItem, type Paginated } from '@24goods/shared'
import { apiV5, client } from '../api'
import type { AdminAnnouncement, AnnouncementCategory } from '../api-v5'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import { usePaged } from '../composables/usePaged'
import { useAuthStore } from '../stores/auth'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const auth = useAuthStore()
const CATS: AnnouncementCategory[] = ['news', 'operations', 'inventory', 'notice']

// ---- my view (every staff member)
const mine = ref<AnnouncementItem[]>([])
const unread = ref(0)
async function loadMine() {
  try {
    const r = await client.get<Paginated<AnnouncementItem> & { unread: number | null }>('/announcements', { page_size: 50 })
    mine.value = r.items
    unread.value = r.unread ?? 0
  } catch (e) {
    toastError(e)
  }
}
async function markAll() {
  try {
    await client.post('/announcements/read-all', {})
    await loadMine()
  } catch (e) {
    toastError(e)
  }
}
async function markOne(a: AnnouncementItem) {
  if (a.read) return
  try {
    await client.post(`/announcements/${encodeURIComponent(a.id)}/read`, {})
    await loadMine()
  } catch (e) {
    toastError(e)
  }
}

// ---- management (admin)
const table = usePaged<AdminAnnouncement>((page, page_size) => apiV5.announcements.list({ page, page_size }))
const cfg = reactive({ prompt_new: true, show_badge: true })
const titleOf = (a: { title_i18n: Record<string, string> }) => (locale.value === 'zh-CN' && a.title_i18n['zh-CN']) || a.title_i18n.en
onMounted(async () => {
  void loadMine()
  if (!auth.isAdmin) return
  void table.load()
  try {
    Object.assign(cfg, await apiV5.announcements.getSettings())
  } catch {
    /* defaults */
  }
})
async function saveCfg(patch: { prompt_new?: boolean; show_badge?: boolean }) {
  try {
    Object.assign(cfg, await apiV5.announcements.setSettings(patch))
    toastOk(t('news.saved'))
  } catch (e) {
    toastError(e)
  }
}

const dlg = ref(false)
const editing = ref<AdminAnnouncement | null>(null)
const tab = ref<'en' | 'zh-CN'>('en')
const saving = ref(false)
const errors = ref<Record<string, string>>({})
const form = reactive({ category: 'news' as AnnouncementCategory, title_en: '', title_zh: '', body_en: '', body_zh: '', status: 'draft' as 'draft' | 'published', pinned: false })
function openEditor(a: AdminAnnouncement | null) {
  editing.value = a
  tab.value = 'en'
  errors.value = {}
  form.category = a?.category ?? 'news'
  form.title_en = a?.title_i18n.en ?? ''
  form.title_zh = a?.title_i18n['zh-CN'] ?? ''
  form.body_en = a?.body_i18n.en ?? ''
  form.body_zh = a?.body_i18n['zh-CN'] ?? ''
  form.status = a?.status ?? 'draft'
  form.pinned = a?.pinned ?? false
  dlg.value = true
}
async function save() {
  if (saving.value) return
  if (!form.title_en.trim()) {
    errors.value = { title: t('news.titleRequired') }
    tab.value = 'en'
    return
  }
  saving.value = true
  const body = {
    category: form.category,
    title_i18n: { en: form.title_en.trim(), 'zh-CN': form.title_zh.trim() },
    body_i18n: { en: form.body_en, 'zh-CN': form.body_zh },
    status: form.status,
    pinned: form.pinned,
  }
  try {
    if (editing.value) await apiV5.announcements.update(editing.value.id, body)
    else await apiV5.announcements.create(body)
    toastOk(t('news.saved'))
    dlg.value = false
    void table.load()
    void loadMine()
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}
async function quick(a: AdminAnnouncement, patch: { status?: 'draft' | 'published'; pinned?: boolean }) {
  try {
    await apiV5.announcements.update(a.id, patch)
    void table.load()
    void loadMine()
  } catch (e) {
    toastError(e)
  }
}
async function remove(a: AdminAnnouncement) {
  if (!(await confirmBox(t('news.deleteConfirm', { title: titleOf(a) })))) return
  try {
    await apiV5.announcements.remove(a.id)
    toastOk(t('news.deleted'))
    void table.load()
    void loadMine()
  } catch (e) {
    toastError(e)
  }
}
const hasUnread = computed(() => unread.value > 0)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h3 class="h">
        {{ t('news.mine') }}
        <el-tag v-if="hasUnread" size="small" type="danger">{{ t('news.unread', { n: unread }) }}</el-tag>
      </h3>
      <el-button :disabled="!hasUnread" @click="markAll">{{ t('news.markAll') }}</el-button>
    </div>
    <div class="panel">
      <el-table :data="mine" row-key="id" :empty-text="t('common.empty')">
        <el-table-column :label="t('products.col.title')" min-width="260">
          <template #default="{ row }">
            <span :class="{ unread: !row.read }">{{ row.title }}</span>
            <el-tag v-if="row.pinned" size="small" type="warning" class="gap-l">{{ t('news.pinned') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('news.category')" width="130"><template #default="{ row }">{{ t(`news.cat.${row.category}`) }}</template></el-table-column>
        <el-table-column :label="t('news.publishedAt')" width="170"><template #default="{ row }">{{ formatDateTime(row.published_at, locale) }}</template></el-table-column>
        <el-table-column :label="t('common.actions')" width="140">
          <template #default="{ row }"><el-button link type="primary" :disabled="row.read" @click="markOne(row as AnnouncementItem)">{{ t('news.markRead') }}</el-button></template>
        </el-table-column>
      </el-table>
    </div>

    <template v-if="auth.isAdmin">
      <div class="page-head gap-t">
        <h3 class="h">{{ t('news.manage') }}</h3>
        <el-button type="primary" :icon="Plus" @click="openEditor(null)">{{ t('news.new') }}</el-button>
      </div>
      <div class="panel cfg">
        <el-switch :model-value="cfg.prompt_new" :aria-label="t('news.promptNew')" @change="(v: string | number | boolean) => saveCfg({ prompt_new: !!v })" />
        <span>{{ t('news.promptNew') }}</span>
        <el-switch :model-value="cfg.show_badge" :aria-label="t('news.showBadge')" @change="(v: string | number | boolean) => saveCfg({ show_badge: !!v })" />
        <span>{{ t('news.showBadge') }}</span>
      </div>
      <div class="panel">
        <el-table v-loading="table.loading.value" :data="table.items.value" row-key="id" :empty-text="t('common.empty')">
          <el-table-column :label="t('products.col.title')" min-width="240"><template #default="{ row }">{{ titleOf(row as AdminAnnouncement) }}</template></el-table-column>
          <el-table-column :label="t('news.category')" width="120"><template #default="{ row }">{{ t(`news.cat.${row.category}`) }}</template></el-table-column>
          <el-table-column :label="t('common.status')" width="110">
            <template #default="{ row }"><el-tag size="small" :type="row.status === 'published' ? 'success' : 'info'">{{ t(`news.status.${row.status}`) }}</el-tag></template>
          </el-table-column>
          <el-table-column :label="t('news.pinned')" width="90"><template #default="{ row }"><el-tag v-if="row.pinned" size="small" type="warning">{{ t('news.pinned') }}</el-tag></template></el-table-column>
          <el-table-column :label="t('news.reads')" prop="read_count" width="90" />
          <el-table-column :label="t('news.publishedAt')" width="170"><template #default="{ row }">{{ formatDateTime(row.published_at, locale) }}</template></el-table-column>
          <el-table-column :label="t('common.actions')" width="330" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEditor(row as AdminAnnouncement)">{{ t('common.edit') }}</el-button>
              <el-button v-if="row.status === 'draft'" link type="primary" @click="quick(row as AdminAnnouncement, { status: 'published' })">{{ t('news.publish') }}</el-button>
              <el-button v-else link type="primary" @click="quick(row as AdminAnnouncement, { status: 'draft' })">{{ t('news.unpublish') }}</el-button>
              <el-button link type="primary" @click="quick(row as AdminAnnouncement, { pinned: !row.pinned })">{{ row.pinned ? t('catalogOrder.unpin') : t('catalogOrder.pin') }}</el-button>
              <el-button link type="danger" @click="remove(row as AdminAnnouncement)">{{ t('common.delete') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" />
        </div>
      </div>

      <el-dialog v-model="dlg" :title="editing ? t('news.edit') : t('news.new')" width="min(780px, 96vw)" destroy-on-close>
        <el-form label-position="top" @submit.prevent="save">
          <el-form-item :label="t('news.category')">
            <el-select v-model="form.category" style="width: 220px">
              <el-option v-for="c in CATS" :key="c" :value="c" :label="t(`news.cat.${c}`)" />
            </el-select>
          </el-form-item>
          <el-tabs v-model="tab">
            <el-tab-pane :label="t('products.form.tabEn')" name="en">
              <el-form-item :label="t('products.form.title')" :error="errors.title" required><el-input v-model="form.title_en" maxlength="150" show-word-limit /></el-form-item>
              <el-form-item :label="t('articles.body')"><MarkdownEditor v-model="form.body_en" :rows="10" :maxlength="20000" /></el-form-item>
            </el-tab-pane>
            <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
              <el-form-item :label="t('products.form.title')"><el-input v-model="form.title_zh" maxlength="150" show-word-limit /></el-form-item>
              <el-form-item :label="t('articles.body')"><MarkdownEditor v-model="form.body_zh" :rows="10" :maxlength="20000" /></el-form-item>
            </el-tab-pane>
          </el-tabs>
          <el-form-item>
            <el-checkbox v-model="form.pinned">{{ t('news.pinned') }}</el-checkbox>
            <el-select v-model="form.status" style="width: 160px; margin-left: 16px">
              <el-option value="draft" :label="t('news.status.draft')" />
              <el-option value="published" :label="t('news.status.published')" />
            </el-select>
          </el-form-item>
          <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
        </el-form>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.h {
  margin: 0;
  display: flex;
  gap: 8px;
  align-items: center;
}
.gap-t {
  margin-top: 24px;
}
.gap-l {
  margin-left: 8px;
}
.unread {
  font-weight: 700;
}
.cfg {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.cfg span {
  margin-right: 16px;
}
</style>

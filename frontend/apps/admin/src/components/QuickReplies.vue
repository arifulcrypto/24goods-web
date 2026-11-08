<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChatLineSquare, Delete, Edit, Plus } from '@element-plus/icons-vue'
import { isApiError, type ReplyTemplate } from '@24goods/shared'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { confirmBox, toastError, toastOk } from '../ui'

const emit = defineEmits<{ pick: [text: string] }>()
defineProps<{ disabled?: boolean }>()
const { t, tm, rt } = useI18n()
const auth = useAuthStore()
const open = ref(false)
const manageOpen = ref(false)
const saving = ref(false)
const items = ref<ReplyTemplate[]>([])
const editing = ref<ReplyTemplate | null>(null)
const draftTitle = ref('')
const draftBody = ref('')
const draftShared = ref(false)

/** Built-in bilingual starters: always available, nothing to configure. */
const starters = computed(() => (tm('replies.starterList') as { title: string; body: string }[]).map((s) => ({ title: rt(s.title), body: rt(s.body) })))
const shared = computed(() => items.value.filter((i) => i.shared))
const mine = computed(() => items.value.filter((i) => !i.shared))
const isAdmin = computed(() => auth.isAdmin)
const valid = computed(() => !!draftTitle.value.trim() && !!draftBody.value.trim())

async function load() {
  try {
    items.value = await api.replyTemplates.list()
  } catch {
    /* starters still work */
  }
}
onMounted(load)

function pick(text: string) {
  emit('pick', text)
  open.value = false
}
function openNew() {
  editing.value = null
  draftTitle.value = ''
  draftBody.value = ''
  draftShared.value = false
  manageOpen.value = true
}
function openEdit(row: ReplyTemplate) {
  editing.value = row
  draftTitle.value = row.title
  draftBody.value = row.body
  draftShared.value = row.shared
  manageOpen.value = true
}
async function save() {
  if (!valid.value || saving.value) return
  saving.value = true
  try {
    const body = { title: draftTitle.value.trim(), body: draftBody.value.trim(), shared: draftShared.value }
    if (editing.value) await api.replyTemplates.update(editing.value.id, body)
    else await api.replyTemplates.create(body)
    await load()
    manageOpen.value = false
    toastOk(t('replies.saved'))
  } catch (e) {
    if (isApiError(e) && e.code === 'template_limit') ElMessage.warning(t('replies.limit'))
    else toastError(e)
  } finally {
    saving.value = false
  }
}
async function remove(row: ReplyTemplate) {
  if (!(await confirmBox(t('replies.deleteConfirm')))) return
  try {
    await api.replyTemplates.remove(row.id)
    await load()
    toastOk(t('replies.deleted'))
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <el-popover v-model:visible="open" placement="top-start" :width="360" trigger="click">
    <template #reference>
      <el-button text :icon="ChatLineSquare" :disabled="disabled">{{ t('replies.button') }}</el-button>
    </template>
    <div class="qr">
      <div class="qr-head">
        <strong>{{ t('replies.title') }}</strong>
        <el-button text size="small" :icon="Plus" @click="openNew">{{ t('replies.add') }}</el-button>
      </div>
      <p class="qr-hint muted">{{ t('replies.hint') }}</p>
      <div v-if="mine.length" class="qr-group">
        <div class="qr-label">{{ t('replies.mine') }}</div>
        <div v-for="r in mine" :key="r.id" class="qr-row">
          <button type="button" class="qr-pick" :title="r.body" @click="pick(r.body)">{{ r.title }}</button>
          <el-button text size="small" :icon="Edit" :aria-label="t('replies.edit')" @click="openEdit(r)" />
          <el-button text size="small" :icon="Delete" :aria-label="t('common.delete')" @click="remove(r)" />
        </div>
      </div>
      <div v-if="shared.length" class="qr-group">
        <div class="qr-label">{{ t('replies.shared') }}</div>
        <div v-for="r in shared" :key="r.id" class="qr-row">
          <button type="button" class="qr-pick" :title="r.body" @click="pick(r.body)">{{ r.title }}</button>
          <template v-if="isAdmin">
            <el-button text size="small" :icon="Edit" :aria-label="t('replies.edit')" @click="openEdit(r)" />
            <el-button text size="small" :icon="Delete" :aria-label="t('common.delete')" @click="remove(r)" />
          </template>
        </div>
      </div>
      <p v-if="!mine.length && !shared.length" class="qr-hint muted">{{ t('replies.none') }}</p>
      <div class="qr-group">
        <div class="qr-label">{{ t('replies.starters') }}</div>
        <div v-for="s in starters" :key="s.title" class="qr-row">
          <button type="button" class="qr-pick" :title="s.body" @click="pick(s.body)">{{ s.title }}</button>
        </div>
      </div>
    </div>
  </el-popover>

  <el-dialog v-model="manageOpen" :title="editing ? t('replies.edit') : t('replies.add')" width="460px" append-to-body>
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item :label="t('replies.name')">
        <el-input v-model="draftTitle" maxlength="60" show-word-limit :placeholder="t('replies.namePlaceholder')" />
      </el-form-item>
      <el-form-item :label="t('replies.body')">
        <el-input v-model="draftBody" type="textarea" :rows="5" maxlength="2000" show-word-limit :placeholder="t('replies.bodyPlaceholder')" />
      </el-form-item>
      <el-form-item v-if="isAdmin">
        <el-checkbox v-model="draftShared">{{ t('replies.shareWithTeam') }}</el-checkbox>
        <div class="qr-hint muted">{{ t('replies.shareHint') }}</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="manageOpen = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="!valid" @click="save">{{ t('common.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.qr {
  max-height: 420px;
  overflow: auto;
}
.qr-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.qr-hint {
  font-size: 12px;
  margin: 4px 0 8px;
  line-height: 1.4;
}
.qr-group + .qr-group {
  margin-top: 10px;
}
.qr-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 2px;
}
.qr-row {
  display: flex;
  align-items: center;
  gap: 8px; /* keep delete away from the pick target */
}
.qr-pick {
  flex: 1;
  min-width: 0;
  min-height: 32px;
  text-align: left;
  border: 0;
  border-radius: 4px;
  background: none;
  font: inherit;
  font-size: 13px;
  color: var(--el-text-color-primary);
  cursor: pointer;
  padding: 4px 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qr-pick:hover {
  background: var(--el-fill-color-light);
}
.qr-pick:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}
</style>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Plus, Search } from '@element-plus/icons-vue'
import { formatDateTime, isApiError, visibleEmail } from '@24goods/shared'
import { apiV5 } from '../api'
import type { AdminUserRow, BlockedIp } from '../api-v5'
import { usePaged } from '../composables/usePaged'
import { useAuthStore } from '../stores/auth'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const route = useRoute()
const auth = useAuthStore()
const tab = ref<'users' | 'blocked'>(auth.isAdmin && route.query.tab === 'blocked' ? 'blocked' : 'users')

// ---- users ----
const f = reactive({ q: typeof route.query.q === 'string' ? route.query.q : '', role: '', status: '' })
const users = usePaged<AdminUserRow>((page, page_size) => apiV5.users.list({ q: f.q.trim(), role: f.role, status: f.status, page, page_size }))
const dlg = ref(false)
const target = ref<AdminUserRow | null>(null)
const reason = ref('')
const until = ref<Date | null>(null)
const saving = ref(false)
const emailOf = (u: AdminUserRow) => visibleEmail(u.email) || u.handle || u.display_name
const canSuspend = (u: AdminUserRow) => u.role !== 'admin' && (auth.isAdmin || u.role === 'buyer')

function openSuspend(u: AdminUserRow) {
  target.value = u
  reason.value = ''
  until.value = null
  dlg.value = true
}
async function suspend() {
  if (!target.value || saving.value) return
  if (!reason.value.trim()) return
  saving.value = true
  try {
    await apiV5.users.suspend(target.value.id, { reason: reason.value.trim(), until: until.value ? until.value.toISOString() : null })
    toastOk(t('v5.users.suspended'))
    dlg.value = false
    await users.load()
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}
async function unsuspend(u: AdminUserRow) {
  if (!(await confirmBox(t('v5.users.unsuspendConfirm', { who: emailOf(u) })))) return
  try {
    await apiV5.users.unsuspend(u.id)
    toastOk(t('v5.users.unsuspended'))
    await users.load()
  } catch (e) {
    toastError(e)
  }
}

// ---- blocked IPs (admin) ----
const blocks = usePaged<BlockedIp>((page, page_size) => apiV5.blockedIps.list({ page, page_size }))
const bdlg = ref(false)
const bf = reactive({ cidr: '', note: '', expires: null as Date | null })
const bErr = ref('')
const invalid = computed(() => !bf.cidr.trim())
function openBlock(ip = '') {
  bf.cidr = ip
  bf.note = ''
  bf.expires = null
  bErr.value = ''
  bdlg.value = true
}
async function addBlock() {
  if (saving.value || invalid.value) return
  saving.value = true
  bErr.value = ''
  try {
    await apiV5.blockedIps.add({ cidr: bf.cidr.trim(), note: bf.note.trim(), expires_at: bf.expires ? bf.expires.toISOString() : null })
    toastOk(t('v5.users.blockAdded'))
    bdlg.value = false
    await blocks.load()
  } catch (e) {
    if (isApiError(e) && e.code === 'validation') bErr.value = t('v5.users.cidrInvalid')
    else toastError(e)
  } finally {
    saving.value = false
  }
}
async function removeBlock(b: BlockedIp) {
  if (!(await confirmBox(t('v5.users.unblockConfirm', { cidr: b.cidr })))) return
  try {
    await apiV5.blockedIps.remove(b.id)
    await blocks.load()
  } catch (e) {
    toastError(e)
  }
}

onMounted(() => {
  void users.load()
  if (auth.isAdmin) {
    void blocks.load()
    if (tab.value === 'blocked' && typeof route.query.ip === 'string') openBlock(route.query.ip)
  }
})
</script>

<template>
  <div class="page">
    <el-tabs v-model="tab">
      <el-tab-pane :label="t('v5.users.tab')" name="users">
        <div class="page-head filters">
          <el-input v-model="f.q" :placeholder="t('v5.users.search')" clearable style="width: 260px" @keyup.enter="users.reload()" @clear="users.reload()" />
          <el-select v-if="auth.isAdmin" v-model="f.role" clearable :placeholder="t('v5.users.role')" style="width: 130px" @change="users.reload()">
            <el-option v-for="r in ['buyer', 'agent', 'admin']" :key="r" :value="r" :label="t(`v5.users.roles.${r}`)" />
          </el-select>
          <el-select v-model="f.status" clearable :placeholder="t('v5.users.status')" style="width: 140px" @change="users.reload()">
            <el-option value="active" :label="t('v5.users.st.active')" />
            <el-option value="suspended" :label="t('v5.users.st.suspended')" />
          </el-select>
          <el-button :icon="Search" @click="users.reload()">{{ t('v5.audit.search') }}</el-button>
        </div>
        <div class="panel">
          <el-table v-loading="users.loading.value" :data="users.items.value" row-key="id" :empty-text="t('common.empty')">
            <el-table-column :label="t('v5.users.user')" min-width="220">
              <template #default="{ row }">
                <div>{{ row.display_name }}</div>
                <div class="muted small">{{ visibleEmail(row.email) || (row.handle ? `@${row.handle}` : '—') }}</div>
              </template>
            </el-table-column>
            <el-table-column :label="t('v5.users.role')" width="100"><template #default="{ row }">{{ t(`v5.users.roles.${row.role}`) }}</template></el-table-column>
            <el-table-column :label="t('v5.users.status')" min-width="190">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'suspended' ? 'danger' : 'success'">{{ t(`v5.users.st.${row.status}`) }}</el-tag>
                <div v-if="row.status === 'suspended'" class="muted small">
                  {{ row.suspend_reason }}<template v-if="row.suspended_until"> · {{ t('v5.users.until', { v: formatDateTime(row.suspended_until, locale) }) }}</template>
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="t('v5.users.created')" width="160"><template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template></el-table-column>
            <el-table-column :label="t('v5.users.lastLogin')" width="160"><template #default="{ row }">{{ row.last_login_at ? formatDateTime(row.last_login_at, locale) : '—' }}</template></el-table-column>
            <el-table-column :label="t('common.actions')" width="120" fixed="right">
              <template #default="{ row }">
                <template v-if="canSuspend(row as AdminUserRow)">
                  <el-button v-if="row.status === 'suspended'" link type="primary" @click="unsuspend(row as AdminUserRow)">{{ t('v5.users.unsuspend') }}</el-button>
                  <el-button v-else link type="danger" @click="openSuspend(row as AdminUserRow)">{{ t('v5.users.suspend') }}</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager"><el-pagination layout="total, prev, pager, next" :total="users.total.value" :page-size="users.pageSize" :current-page="users.page.value" @current-change="users.load" /></div>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="auth.isAdmin" :label="t('v5.users.blockedTab')" name="blocked">
        <div class="page-head">
          <span class="muted">{{ t('v5.users.blockedIntro') }}</span>
          <el-button type="primary" :icon="Plus" @click="openBlock()">{{ t('v5.users.blockNew') }}</el-button>
        </div>
        <div class="panel">
          <el-table v-loading="blocks.loading.value" :data="blocks.items.value" row-key="id" :empty-text="t('common.empty')">
            <el-table-column :label="t('v5.users.cidr')" width="220"><template #default="{ row }"><span class="mono">{{ row.cidr }}</span></template></el-table-column>
            <el-table-column :label="t('v5.promo.note')" prop="note" min-width="200" show-overflow-tooltip />
            <el-table-column :label="t('v5.users.expires')" width="170"><template #default="{ row }">{{ row.expires_at ? formatDateTime(row.expires_at, locale) : t('v5.users.never') }}</template></el-table-column>
            <el-table-column :label="t('v5.users.created')" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template></el-table-column>
            <el-table-column :label="t('common.actions')" width="100" fixed="right">
              <template #default="{ row }"><el-button link type="danger" @click="removeBlock(row as BlockedIp)">{{ t('v5.delete') }}</el-button></template>
            </el-table-column>
          </el-table>
          <div class="pager"><el-pagination layout="total, prev, pager, next" :total="blocks.total.value" :page-size="blocks.pageSize" :current-page="blocks.page.value" @current-change="blocks.load" /></div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dlg" :title="t('v5.users.suspendTitle', { who: target ? emailOf(target) : '' })" width="min(480px, 96vw)" destroy-on-close>
      <el-form label-position="top" @submit.prevent="suspend">
        <el-form-item :label="t('v5.users.reason')"><el-input v-model="reason" maxlength="200" show-word-limit /></el-form-item>
        <el-form-item :label="t('v5.users.untilLabel')">
          <el-date-picker v-model="until" type="datetime" format="YYYY-MM-DD HH:mm" :disabled-date="(d: Date) => d.getTime() < Date.now() - 86400000" style="width: 100%" />
          <div class="muted small">{{ t('v5.users.untilHint') }}</div>
        </el-form-item>
        <div class="muted small">{{ t('v5.users.suspendHint') }}</div>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="saving" :disabled="!reason.trim()" @click="suspend">{{ t('v5.users.suspend') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bdlg" :title="t('v5.users.blockNew')" width="min(480px, 96vw)" destroy-on-close>
      <el-form label-position="top" @submit.prevent="addBlock">
        <el-form-item :label="t('v5.users.cidr')" :error="bErr"><el-input v-model="bf.cidr" class="mono" spellcheck="false" placeholder="203.0.113.0/24" /></el-form-item>
        <el-form-item :label="t('v5.promo.note')"><el-input v-model="bf.note" maxlength="200" show-word-limit /></el-form-item>
        <el-form-item :label="t('v5.users.expires')">
          <el-date-picker v-model="bf.expires" type="datetime" format="YYYY-MM-DD HH:mm" style="width: 100%" />
          <div class="muted small">{{ t('v5.users.expiresHint') }}</div>
        </el-form-item>
        <div class="muted small">{{ t('v5.users.cidrHint') }}</div>
      </el-form>
      <template #footer>
        <el-button @click="bdlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="saving" :disabled="invalid" @click="addBlock">{{ t('v5.users.block') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filters {
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-start;
}
</style>

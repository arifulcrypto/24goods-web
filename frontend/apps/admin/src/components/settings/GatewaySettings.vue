<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { client } from '../../api'
import { useSave } from '../../composables/useSave'
import { useRead } from '../../composables/useRead'
import { toastError } from '../../ui'
interface Gateway { id: string; label: string; gateway: string; merchant_id: string; mode: string; active: boolean; created_at: string }
const { t } = useI18n()
const { saving, run } = useSave()
const reader=useRead()
const items = ref<Gateway[]>([])
const editing = ref(false)
const activating = ref<Gateway | null>(null)
const password = ref('')
const ownership = ref(false)
const form = reactive({ label: '', gateway: 'https://pay.xiaodouap.cn/submit.php', merchant_id: '', secret: '', mode: 'qr' as 'qr' | 'redirect', current_password: '' })
type Health = { status: 'healthy' | 'degraded' | 'unreachable'; last_test: { ok: boolean; at: string; latency_ms: number | null } | null }
const health = ref<Record<string, Health>>({})
const usdt = ref<Health | null>(null)
const testing = ref('')
const testMsg = ref<Record<string, string>>({})
async function loadHealth() {
  try {
    const r = await client.get<{ gateways: Record<string, Health>; bepusdt: Health | null }>('/admin/payment-gateways-health')
    health.value = r.gateways
    usdt.value = r.bepusdt
  } catch { /* advisory */ }
}
async function load() { const r = await reader.run(() => client.get<{items: Gateway[]}>('/admin/payment-gateways')); if (r) items.value = r.items; await loadHealth() }
onMounted(load)
interface TestResult { ok: boolean; reachable: boolean; latency_ms?: number; message?: string }
async function runTest(id: string) {
  testing.value = id
  try {
    const r = await client.post<TestResult>(`/admin/payment-gateways/${encodeURIComponent(id)}/test`, {})
    testMsg.value[id] = r.ok ? t('gateways.tested', { ms: r.latency_ms ?? 0, msg: r.message ?? '' }) : t('gateways.testFailed')
    await loadHealth()
  } catch (e) { toastError(e) } finally { testing.value = '' }
}
async function create() {
  const r = await run(() => client.post('/admin/payment-gateways', { ...form }))
  // Clear authentication material on every submission, including errors.
  form.secret = ''; form.current_password = ''
  if (r) { editing.value = false; await load() }
}
function activate(row: Gateway) { activating.value = row; password.value = ''; ownership.value = false }
async function confirmActivate() {
  if (!activating.value) return
  const r = await run(() => client.post(`/admin/payment-gateways/${activating.value!.id}/activate`, { current_password: password.value, ownership_confirmed: ownership.value }))
  password.value = ''
  if (r) { activating.value = null; await load() }
}
function close() { form.secret = ''; form.current_password = ''; editing.value = false }
</script>
<template>
  <section class="panel block">
    <h2 class="h2">{{ t('gateways.title') }}</h2>
    <p class="muted small">{{ t('gateways.note') }}</p>
    <el-alert v-if="items.some(row => Date.now() - Date.parse(row.created_at) > 90*86400000)" :title="t('gateways.review')" type="warning" :closable="false" />
    <el-alert :title="t('gateways.privacy')" type="info" :closable="false" show-icon />
    <el-table :data="items" row-key="id" class="gateways">
      <el-table-column prop="label" :label="t('gateways.label')" />
      <el-table-column prop="gateway" :label="t('gateways.url')" min-width="200" />
      <el-table-column :label="t('gateways.pid')"><template #default="{ row }">••{{ row.merchant_id.slice(-4) }}</template></el-table-column>
      <el-table-column prop="created_at" :label="t('gateways.created')" min-width="160" />
      <el-table-column :label="t('gateways.status')"><template #default="{ row }">{{ row.active ? t('gateways.active') : t('gateways.retained') }}</template></el-table-column>
      <el-table-column :label="t('gateways.health.title')" min-width="140"><template #default="{ row }">
        <el-tag :type="health[row.id]?.status === 'healthy' ? 'success' : health[row.id]?.status === 'degraded' ? 'warning' : health[row.id]?.status === 'unreachable' ? 'danger' : 'info'" effect="plain">{{ t(`gateways.health.${health[row.id]?.status ?? 'unknown'}`) }}</el-tag>
        <div v-if="testMsg[row.id]" class="muted small">{{ testMsg[row.id] }}</div>
      </template></el-table-column>
      <el-table-column :label="t('gateways.actions')" min-width="180"><template #default="{ row }">
        <el-button size="small" :loading="testing === row.id" @click="runTest(row.id)">{{ t('gateways.test') }}</el-button>
        <el-button v-if="!row.active" size="small" :disabled="saving" @click="activate(row as Gateway)">{{ t('gateways.activate') }}</el-button>
      </template></el-table-column>
    </el-table>
    <p class="muted small">{{ t('gateways.testNote') }}</p>
    <p v-if="usdt" class="muted small">{{ t('gateways.usdtTitle') }}:
      <el-tag size="small" :type="usdt.status === 'healthy' ? 'success' : 'danger'" effect="plain">{{ t(`gateways.health.${usdt.status}`) }}</el-tag>
      <el-button size="small" :loading="testing === 'bepusdt'" @click="runTest('bepusdt')">{{ t('gateways.test') }}</el-button>
    </p>
    <el-button @click="editing = true">{{ t('gateways.add') }}</el-button>
    <el-dialog :model-value="editing" :title="t('gateways.add')" width="min(92vw, 600px)" @close="close">
      <el-form label-position="top" @submit.prevent="create">
        <el-form-item :label="t('gateways.label')" required><el-input v-model="form.label" maxlength="80" /></el-form-item>
        <el-form-item :label="t('gateways.url')" required><el-input v-model="form.gateway" maxlength="500" /></el-form-item>
        <el-form-item :label="t('gateways.pid')" required><el-input v-model="form.merchant_id" maxlength="64" autocomplete="off" /></el-form-item>
        <el-form-item :label="t('gateways.key')" required><el-input v-model="form.secret" type="password" maxlength="256" autocomplete="new-password" /></el-form-item>
        <el-form-item :label="t('gateways.mode')"><el-select v-model="form.mode"><el-option value="qr" :label="t('gateways.qr')" /><el-option value="redirect" :label="t('gateways.redirect')" /></el-select></el-form-item>
        <el-form-item :label="t('gateways.password')" required><el-input v-model="form.current_password" type="password" autocomplete="current-password" /></el-form-item>
        <p>{{ t('gateways.rotation') }}</p>
        <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
      </el-form>
    </el-dialog>
    <el-dialog :model-value="!!activating" :title="t('gateways.activate')" width="min(92vw, 600px)" @close="activating = null; password = ''">
      <el-form label-position="top" @submit.prevent="confirmActivate">
        <p>{{ t('gateways.confirmNote') }}</p>
        <el-checkbox v-model="ownership">{{ t('gateways.ownership') }}</el-checkbox>
        <el-form-item :label="t('gateways.password')"><el-input v-model="password" type="password" autocomplete="current-password" /></el-form-item>
        <el-button type="primary" native-type="submit" :disabled="!ownership || !password" :loading="saving">{{ t('gateways.activate') }}</el-button>
      </el-form>
    </el-dialog>
  </section>
</template>
<style scoped>.gateways { margin: 16px 0 }</style>

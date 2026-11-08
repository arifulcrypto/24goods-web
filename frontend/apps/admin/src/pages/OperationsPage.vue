<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatBytes } from '@24goods/shared'
import { client } from '../api'
import { useSave } from '../composables/useSave'
interface Overview { collected_at:string; environment:string; release:{commit:string|null}; resources:{process:{rss_bytes:number|null;uptime_seconds:number};cgroup:{memory_current:number|null;memory_max:number|null};disk:{free_bytes:number;free_percent:number;free_inodes:number}|null;load:{averages:number[]|null};recovery:{status:string;last_complete_at:string|null;offsite_verified:boolean|null;last_restore_test_at:string|null}}; database:{status:string};redis:{status:string};outbox:{pending:number|null;oldest_at:string|null;failed:number|null};payment_review:{orders:number|null;uncertain_attempts:number|null} }
interface Event {id:number;family:string;action:string;outcome:string;reason:string;resource_id:string|null;actor_id:string|null;occurred_at:string;ip:string|null;user_agent:string|null}
const { t, locale } = useI18n()
const { run, saving } = useSave()
const data = ref<Overview|null>(null)
const items = ref<Event[]>([])
const family = ref('')
const page = ref(1)
const total = ref(0)
const stale = computed(() => !data.value || Date.now()-Date.parse(data.value.collected_at)>90000)
async function load() { const r = await run(() => client.get<Overview>('/admin/operations')); if(r) data.value=r }
async function logs() { const r = await run(() => client.get<{items:Event[];total:number}>('/admin/operations/events',{family:family.value||undefined,page:page.value})); if(r){ items.value=r.items;total.value=r.total } }
function refresh() { void load(); void logs() }
onMounted(refresh)
const timer = setInterval(() => { if(!document.hidden) void load() },60000)
onBeforeUnmount(()=>clearInterval(timer))
</script>
<template>
  <div class="operations">
    <div class="head"><h1>{{ t('ops.title') }}</h1><el-button :loading="saving" @click="refresh">{{ t('ops.refresh') }}</el-button></div>
    <p>{{ t('ops.scope') }}</p>
    <el-alert v-if="stale" type="warning" :title="t('ops.stale')" :closable="false" />
    <template v-if="data">
      <p><el-tag>{{ data.environment }}</el-tag> {{ data.release.commit?.slice(0,12) || t('ops.unknown') }} · {{ data.collected_at }}</p>
      <div class="grid">
        <el-card><h2>{{ t('ops.memory') }}</h2><p>{{ data.resources.cgroup.memory_current === null ? t('ops.unknown') : formatBytes(data.resources.cgroup.memory_current, locale) }} / {{ data.resources.cgroup.memory_max === null ? t('ops.unknown') : formatBytes(data.resources.cgroup.memory_max, locale) }}</p><p>{{ t('ops.process') }} {{ data.resources.process.rss_bytes === null ? t('ops.unknown') : formatBytes(data.resources.process.rss_bytes, locale) }}</p></el-card>
        <el-card><h2>{{ t('ops.disk') }}</h2><p>{{ data.resources.disk ? formatBytes(data.resources.disk.free_bytes, locale) : t('ops.unknown') }}</p><p>{{ t('ops.inodes') }} {{ data.resources.disk?.free_inodes ?? t('ops.unknown') }}</p></el-card>
        <el-card><h2>{{ t('ops.dependencies') }}</h2><p>PostgreSQL: {{ data.database.status }}</p><p>Redis: {{ data.redis.status }}</p><p>{{ t('ops.load') }} {{ data.resources.load.averages?.join(' / ') ?? t('ops.unknown') }}</p></el-card>
        <el-card><h2>{{ t('ops.queue') }}</h2><p>{{ t('ops.pending') }} {{ data.outbox.pending ?? t('ops.unknown') }}</p><p>{{ t('ops.failed') }} {{ data.outbox.failed ?? t('ops.unknown') }}</p><p>{{ t('ops.paymentReview') }} {{ data.payment_review.orders ?? t('ops.unknown') }}</p><p>{{t('ops.uncertain')}} {{data.payment_review.uncertain_attempts ?? t('ops.unknown')}}</p></el-card>
        <el-card><h2>{{ t('ops.recovery') }}</h2><p>{{ t('ops.lastBackup') }} {{ data.resources.recovery.last_complete_at ?? t('ops.unknown') }}</p><p>{{ t('ops.offsite') }} {{ data.resources.recovery.offsite_verified ?? t('ops.unknown') }}</p><p>{{ t('ops.lastRestore') }} {{ data.resources.recovery.last_restore_test_at ?? t('ops.unknown') }}</p></el-card>
        <el-card><h2>{{ t('ops.maintenance') }}</h2><p>{{ t('ops.maintenanceNote') }}</p><RouterLink to="/settings/payment">{{ t('ops.reviewKeys') }}</RouterLink></el-card>
      </div>
    </template>
    <h2>{{ t('ops.events') }}</h2>
    <el-select v-model="family" :placeholder="t('ops.family')" clearable @change="page=1; logs()"><el-option v-for="f in ['operation','login','security','system','task','payment']" :key="f" :value="f" :label="t(`ops.${f}`)" /></el-select>
    <el-table :data="items" row-key="id">
      <el-table-column prop="occurred_at" :label="t('ops.date')" min-width="180" />
      <el-table-column prop="family" :label="t('ops.family')" />
      <el-table-column prop="action" :label="t('ops.action')" min-width="150" />
      <el-table-column prop="outcome" :label="t('ops.status')" />
      <el-table-column prop="reason" :label="t('ops.reason')" />
      <el-table-column prop="resource_id" :label="t('ops.resource')" />
      <el-table-column prop="actor_id" :label="t('ops.actor')" />
      <el-table-column prop="ip" label="IP" />
      <el-table-column prop="user_agent" :label="t('ops.agent')" show-overflow-tooltip />
    </el-table>
    <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="logs" />
    <p>{{ t('ops.hints') }}</p>
  </div>
</template>
<style scoped>.head{display:flex;justify-content:space-between;align-items:center}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px;margin:16px 0}h2{font-size:17px}.el-select{max-width:240px}.operations{overflow:auto}</style>

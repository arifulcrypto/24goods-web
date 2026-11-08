<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { client } from '../api'
import { useAuthStore } from '../stores/auth'
import { useSave } from '../composables/useSave'
import { useRead } from '../composables/useRead'
interface Service { id:string; label:string; buyer_id:string;order_id:string;owner_id:string;status:string;starts_at:string|null;ends_at:string|null;timezone:string;version:number;overdue:boolean;email_reminders:boolean;telegram_reminders:boolean }
const { t }=useI18n()
const auth=useAuthStore()
const { run,saving }=useSave()
const reader=useRead()
const rows=ref<Service[]>([]),total=ref(0),page=ref(1),due=ref(''),mine=ref(false),adding=ref(false)
const form=reactive({buyer_id:'',order_id:'',owner_id:auth.user?.id??'',label:'',timezone:'UTC',period_policy:'calendar_month',period_days:30,grace_days:0,email_reminders:false,telegram_reminders:false})
const editing=ref<Service|null>(null), reason=ref(''), end=ref(''), renewal=ref('')
async function load(){ const r=await reader.run(()=>client.get<{items:Service[];total:number}>('/admin/services',{page:page.value,due:due.value||undefined,mine:mine.value}));if(r){rows.value=r.items;total.value=r.total} }
const route=useRoute()
onMounted(async()=>{await load();if(typeof route.params.id==='string'){const row=await reader.run(()=>client.get<Service>(`/admin/services/${encodeURIComponent(String(route.params.id))}`));if(row)edit(row)}})
async function create(){const r=await run(()=>client.post('/admin/services',{...form}));if(r){adding.value=false;await load()} }
async function activate(row:Service){const r=await run(()=>client.post(`/admin/services/${row.id}/activate`,{}));if(r)await load()}
function edit(row:Service){editing.value={...row};reason.value='';end.value=row.ends_at??'';renewal.value=''}
async function update(cancel=false){if(!editing.value)return;const r=await run(()=>client.patch(`/admin/services/${editing.value!.id}`,{expected_version:editing.value!.version,reason:reason.value,...(cancel?{status:'cancelled'}:{...(end.value.trim() && end.value !== editing.value!.ends_at ? {ends_at:end.value.trim()} : {}),email_reminders:editing.value!.email_reminders,telegram_reminders:editing.value!.telegram_reminders})}));if(r){editing.value=null;await load()}}
async function renew(){if(!editing.value)return;const r=await run(()=>client.post(`/admin/services/${editing.value!.id}/renewals`,{order_id:renewal.value,reason:reason.value}));if(r){editing.value=null;await load()}}
</script>
<template>
  <div>
    <h1>{{ t('services.title') }}</h1><p>{{ t('services.note') }}</p>
    <div class="tools"><el-button type="primary" @click="adding=true">{{ t('services.add') }}</el-button><el-select v-model="due" clearable :placeholder="t('services.filter')" @change="page=1;load()"><el-option value="upcoming" :label="t('services.upcoming')"/><el-option value="overdue" :label="t('services.overdue')"/></el-select><el-checkbox v-model="mine" @change="page=1;load()">{{ t('services.mine') }}</el-checkbox><el-button :loading="saving" @click="load">{{ t('ops.refresh') }}</el-button></div>
    <el-table :data="rows" row-key="id">
      <el-table-column prop="label" :label="t('services.label')"/>
      <el-table-column prop="status" :label="t('ops.status')"><template #default="{row}">{{t(`ops.states.${row.status}`)}}</template></el-table-column>
      <el-table-column prop="owner_id" :label="t('services.owner')" show-overflow-tooltip/>
      <el-table-column prop="ends_at" :label="t('services.end')" min-width="190"/>
      <el-table-column prop="timezone" :label="t('services.timezone')"/>
      <el-table-column :label="t('gateways.actions')"><template #default="{row}"><el-button v-if="row.status==='pending'" @click="activate(row as Service)">{{t('services.activate')}}</el-button><el-button @click="edit(row as Service)">{{t('services.manage')}}</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="load"/>
    <el-dialog v-model="adding" :title="t('services.add')" width="min(94vw,620px)"><el-form label-position="top" @submit.prevent="create">
      <el-form-item :label="t('services.label')"><el-input v-model="form.label" maxlength="120"/></el-form-item>
      <el-form-item :label="t('services.buyer')"><el-input v-model="form.buyer_id"/></el-form-item>
      <el-form-item :label="t('services.order')"><el-input v-model="form.order_id"/></el-form-item>
      <el-form-item :label="t('services.owner')"><el-input v-model="form.owner_id"/></el-form-item>
      <el-form-item :label="t('services.timezone')"><el-input v-model="form.timezone"/></el-form-item>
      <el-form-item :label="t('services.policy')"><el-select v-model="form.period_policy"><el-option value="calendar_month" :label="t('services.month')"/><el-option value="fixed_days" :label="t('services.days')"/></el-select></el-form-item>
      <el-form-item v-if="form.period_policy==='fixed_days'" :label="t('services.days')"><el-input-number v-model="form.period_days" :min="1" :max="366"/></el-form-item>
      <el-checkbox v-model="form.email_reminders">{{t('services.email')}}</el-checkbox><el-checkbox v-model="form.telegram_reminders">{{t('services.telegram')}}</el-checkbox>
      <p>{{t('services.activationNote')}}</p><el-button type="primary" native-type="submit" :loading="saving">{{t('common.save')}}</el-button>
    </el-form></el-dialog>
    <el-dialog :model-value="!!editing" :title="t('services.manage')" width="min(94vw,620px)" @close="editing=null">
      <el-form v-if="editing" label-position="top" @submit.prevent="update()">
        <el-form-item :label="t('services.reason')"><el-input v-model="reason" maxlength="200"/></el-form-item>
        <el-form-item :label="t('services.endIso')"><el-input v-model="end"/></el-form-item>
        <el-checkbox v-model="editing.email_reminders">{{t('services.email')}}</el-checkbox><el-checkbox v-model="editing.telegram_reminders">{{t('services.telegram')}}</el-checkbox>
        <div class="tools"><el-button native-type="submit" :disabled="!reason" :loading="saving">{{t('common.save')}}</el-button><el-button type="danger" :disabled="!reason" :loading="saving" @click="update(true)">{{t('services.cancel')}}</el-button></div>
        <el-divider/>
        <p>{{t('services.renewalNote')}}</p><el-form-item :label="t('services.renewalOrder')"><el-input v-model="renewal"/></el-form-item><el-button :disabled="!reason||!renewal" :loading="saving" @click="renew">{{t('services.renew')}}</el-button>
      </el-form>
    </el-dialog>
  </div>
</template>
<style scoped>.tools{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:12px 0}.tools .el-select{max-width:230px}</style>

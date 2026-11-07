<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { copyText, formatDateTime } from '@24goods/shared'
import { apiV5 } from '../../api'
import { API_TOKEN_SCOPES, type ApiTokenRow, type ApiTokenScope } from '../../api-v5'
import { confirmBox, toastError, toastOk } from '../../ui'

const { t, locale } = useI18n()
const rows = ref<ApiTokenRow[]>([])
const loading = ref(false)
const creating = ref(false)
const form = reactive({ name: '', scopes: [] as ApiTokenScope[], days: 90 })
const shown = ref<string | null>(null) // plaintext, held in memory only until dismissed

async function load() {
  loading.value = true
  try {
    rows.value = (await apiV5.apiTokens.list()).items
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
}
async function create() {
  if (creating.value || !form.name.trim() || !form.scopes.length) return
  creating.value = true
  try {
    const r = await apiV5.apiTokens.create({ name: form.name.trim(), scopes: form.scopes, expires_in_days: form.days })
    shown.value = r.token
    form.name = ''
    form.scopes = []
    await load()
  } catch (e) {
    toastError(e)
  } finally {
    creating.value = false
  }
}
async function copy() {
  if (shown.value && (await copyText(shown.value))) toastOk(t('v5.tokens.copied'))
}
async function revoke(row: ApiTokenRow) {
  if (!(await confirmBox(t('v5.tokens.revokeConfirm')))) return
  try {
    await apiV5.apiTokens.revoke(row.id)
    toastOk(t('v5.tokens.revoked'))
    await load()
  } catch (e) {
    toastError(e)
  }
}
onMounted(load)
</script>

<template>
  <div class="wrap">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.tokens.title') }}</h2>
      <p class="muted small">{{ t('v5.tokens.note') }}</p>
      <p class="muted small">{{ t('v5.tokens.docs') }}</p>
      <el-alert v-if="shown" type="warning" :closable="false" show-icon :title="t('v5.tokens.shownOnce')" class="once">
        <code class="tok">{{ shown }}</code>
        <div class="row">
          <el-button type="primary" @click="copy">{{ t('v5.tokens.copy') }}</el-button>
          <el-button @click="shown = null">{{ t('v5.tokens.done') }}</el-button>
        </div>
      </el-alert>
      <el-form label-position="top" @submit.prevent="create">
        <el-form-item :label="t('v5.tokens.name')">
          <el-input v-model="form.name" maxlength="60" :placeholder="t('v5.tokens.namePh')" />
        </el-form-item>
        <el-form-item :label="t('v5.tokens.scopes')">
          <el-checkbox-group v-model="form.scopes" class="scopes">
            <el-checkbox v-for="s in API_TOKEN_SCOPES" :key="s" :value="s">{{ t(`v5.tokens.scope.${s}`) }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item :label="t('v5.tokens.expires')">
          <el-input-number v-model="form.days" :min="1" :max="365" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="creating" :disabled="!form.name.trim() || !form.scopes.length">
          {{ t('v5.tokens.create') }}
        </el-button>
      </el-form>
    </section>
    <section class="panel block">
      <el-table v-loading="loading" :data="rows" :empty-text="t('v5.tokens.empty')">
        <el-table-column :label="t('v5.tokens.name')" prop="name" min-width="140" />
        <el-table-column :label="t('v5.tokens.prefix')" min-width="110">
          <template #default="{ row }"><code>{{ row.prefix }}…</code></template>
        </el-table-column>
        <el-table-column :label="t('v5.tokens.scopes')" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="s in row.scopes" :key="s" size="small" class="tag">{{ s }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('v5.tokens.lastUsed')" min-width="150">
          <template #default="{ row }">{{ row.last_used_at ? formatDateTime(row.last_used_at, locale) : t('v5.tokens.never') }}</template>
        </el-table-column>
        <el-table-column :label="t('v5.tokens.expiresAt')" min-width="150">
          <template #default="{ row }">{{ row.expires_at ? formatDateTime(row.expires_at, locale) : '-' }}</template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="100">
          <template #default="{ row }">{{ t(`v5.tokens.status.${row.status}`) }}</template>
        </el-table-column>
        <el-table-column width="100">
          <template #default="{ row }">
            <el-button v-if="row.status === 'active'" link type="danger" @click="revoke(row as ApiTokenRow)">{{ t('v5.tokens.revoke') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped src="./settings.css"></style>
<style scoped>
.once {
  margin: 12px 0;
}
.tok {
  display: block;
  word-break: break-all;
  margin: 8px 0;
  user-select: all;
}
.row {
  display: flex;
  gap: 8px;
}
.scopes {
  display: flex;
  flex-direction: column;
}
.tag {
  margin: 0 4px 4px 0;
}
</style>

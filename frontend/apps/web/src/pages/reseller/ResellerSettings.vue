<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { amountToMinor, fromMinor, goBack, percentToPermille, permilleToPercent, safeAssetUrl, type MarkupKind } from '@24goods/shared'
import { api } from '../../api'
import { useResellerStore } from '../../stores/reseller'
import { showConfirmDialog, showSuccessToast, toastError } from '../../ui'
import type { ResellerDomains } from '@24goods/shared'

const { t } = useI18n()
const router = useRouter()
const store = useResellerStore()
const name = ref('')
const logo = ref<string | null>(null)
const welcomeEn = ref('')
const welcomeZh = ref('')
const contact = ref('')
const kind = ref<MarkupKind>('percent')
const val = ref('')
const payName = ref('')
const payAccount = ref('')
const busy = ref(false)
const dom = ref<ResellerDomains | null>(null)
const newHost = ref('')
const domBusy = ref(false)
async function loadDomains() {
  try {
    dom.value = await api.reseller.domains()
  } catch {
    dom.value = null // feature hidden when unavailable
  }
}
async function addDomain() {
  if (domBusy.value || !newHost.value.trim()) return
  domBusy.value = true
  try {
    await api.reseller.addDomain(newHost.value.trim())
    newHost.value = ''
    await loadDomains()
  } catch (e) {
    toastError(e)
  } finally {
    domBusy.value = false
  }
}
async function checkDomain(id: string) {
  if (domBusy.value) return
  domBusy.value = true
  try {
    await api.reseller.refreshDomain(id)
    await loadDomains()
  } catch (e) {
    toastError(e)
  } finally {
    domBusy.value = false
  }
}
async function removeDomain(id: string) {
  if (domBusy.value) return
  try {
    await showConfirmDialog({ message: t('reseller.domainConfirm') })
  } catch {
    return // cancelled
  }
  domBusy.value = true
  try {
    await api.reseller.removeDomain(id)
    await loadDomains()
  } catch (e) {
    toastError(e)
  } finally {
    domBusy.value = false
  }
}
const uploading = ref(false)
const parsed = computed(() => (kind.value === 'percent' ? percentToPermille(val.value) : amountToMinor(val.value)))
const logoSrc = computed(() => safeAssetUrl(logo.value))

onMounted(async () => {
  void loadDomains()
  try {
    const m = await store.load(true)
    if (!m) return void router.replace('/reseller/apply')
    name.value = m.store_name
    logo.value = m.logo_url
    welcomeEn.value = m.welcome_i18n.en ?? ''
    welcomeZh.value = m.welcome_i18n['zh-CN'] ?? ''
    contact.value = m.contact ?? ''
    kind.value = m.markup_kind
    val.value = m.markup_kind === 'percent' ? permilleToPercent(m.markup_value) : fromMinor(BigInt(m.markup_value)).replace(/\.00$/, '')
    payName.value = m.payout_account?.name ?? ''
    payAccount.value = m.payout_account?.account ?? ''
  } catch (e) {
    toastError(e)
  }
})

async function onLogo(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0]
  await uploadLogo(f)
  ;(ev.target as HTMLInputElement).value = ''
}
async function uploadLogo(f: File | undefined) {
  if (!f || uploading.value) return
  uploading.value = true
  try {
    // existing hostile-upload pipeline (magic bytes, re-encode); the result is our own /media/ URL
    logo.value = (await api.upload(f, { kind: 'image', filename: f.name })).url
  } catch (e) {
    toastError(e)
  } finally {
    uploading.value = false
  }
}
async function save() {
  if (busy.value || parsed.value === null || !name.value.trim()) return
  busy.value = true
  try {
    const welcome: Record<string, string> = {}
    if (welcomeEn.value.trim()) welcome.en = welcomeEn.value.trim()
    if (welcomeZh.value.trim()) welcome['zh-CN'] = welcomeZh.value.trim()
    store.set(
      await api.reseller.update({
        store_name: name.value.trim(),
        logo_url: logo.value,
        welcome_i18n: welcome,
        contact: contact.value.trim() || null,
        markup_kind: kind.value,
        markup_value: parsed.value,
        payout_account: payAccount.value.trim() ? { method: 'alipay', name: payName.value.trim(), account: payAccount.value.trim() } : null,
      }),
    )
    showSuccessToast(t('common.saved'))
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('reseller.settings')" left-arrow fixed placeholder safe-area-inset-top class="dock-top" @click-left="goBack(router, '/reseller')" />
    <van-form class="section card" @submit="save">
      <van-field v-model="name" maxlength="64" :label="t('reseller.storeName')" :rules="[{ required: true, message: t('common.required') }]" />
      <van-cell :title="t('reseller.logo')" center>
        <template #value>
          <label class="logo-pick">
            <img v-if="logoSrc" :src="logoSrc" width="44" height="44" alt="" />
            <span>{{ uploading ? t('common.loading') : t('reseller.chooseLogo') }}</span>
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden @change="onLogo" />
          </label>
        </template>
      </van-cell>
      <van-field v-model="welcomeEn" type="textarea" rows="2" autosize maxlength="300" :label="t('reseller.welcomeEn')" />
      <van-field v-model="welcomeZh" type="textarea" rows="2" autosize maxlength="300" :label="t('reseller.welcomeZh')" />
      <van-field v-model="contact" maxlength="200" :label="t('reseller.contact')" />
      <van-cell :title="t('reseller.defaultMarkup')">
        <template #value>
          <van-radio-group v-model="kind" direction="horizontal">
            <van-radio name="percent">{{ t('reseller.kindPercent') }}</van-radio>
            <van-radio name="fixed">{{ t('reseller.kindFixed') }}</van-radio>
          </van-radio-group>
        </template>
      </van-cell>
      <van-field v-model="val" inputmode="decimal" :label="kind === 'percent' ? '%' : '¥'" :error-message="parsed === null ? t('reseller.invalidNumber') : ''" />
      <h3 class="h">{{ t('reseller.payoutAccount') }}</h3>
      <van-field v-model="payName" maxlength="60" :label="t('reseller.accountName')" />
      <van-field v-model="payAccount" maxlength="100" :label="t('reseller.accountNo')" :placeholder="t('reseller.alipay')" />
      <div class="submit"><van-button round block type="primary" native-type="submit" :loading="busy">{{ t('common.save') }}</van-button></div>
    </van-form>
    <div v-if="dom && dom.enabled" class="section card dom">
      <h3 class="h">{{ t('reseller.domainCard') }}</h3>
      <p class="hint">{{ t('reseller.domainIntro') }}</p>
      <div v-for="d in dom.items" :key="d.id" class="dom-item">
        <div class="dom-head">
          <b>{{ d.hostname }}</b>
          <van-tag :type="d.status === 'active' ? 'success' : d.status === 'failed' ? 'danger' : 'warning'">
            {{ t(d.status === 'active' ? 'reseller.domainActive' : d.status === 'failed' ? 'reseller.domainFailed' : 'reseller.domainPending') }}
          </van-tag>
        </div>
        <template v-if="d.status !== 'active'">
          <p class="rec">{{ t('reseller.domainCname') }}: <code>{{ d.hostname }} → {{ dom.fallback_origin }}</code></p>
          <p v-if="d.verification.ownership?.name" class="rec">
            {{ t('reseller.domainTxt') }}: <code>{{ d.verification.ownership.name }}</code> = <code>{{ d.verification.ownership.value }}</code>
          </p>
          <p v-for="(r, i) in d.verification.ssl_records ?? []" :key="i" class="rec">
            <template v-if="r.txt_name">{{ t('reseller.domainTxt') }}: <code>{{ r.txt_name }}</code> = <code>{{ r.txt_value }}</code></template>
          </p>
        </template>
        <div class="dom-actions">
          <van-button v-if="d.status !== 'active'" class="dom-btn" :loading="domBusy" @click="checkDomain(d.id)">{{ t('reseller.domainCheck') }}</van-button>
          <van-button class="dom-btn" type="danger" plain :disabled="domBusy" @click="removeDomain(d.id)">{{ t('reseller.domainRemove') }}</van-button>
        </div>
      </div>
      <van-field v-if="dom.items.length < dom.max_domains" v-model="newHost" maxlength="253" :placeholder="t('reseller.domainPh')" :aria-label="t('reseller.domainPh')" inputmode="url" autocapitalize="none" autocomplete="off" :spellcheck="false" />
      <div v-if="dom.items.length < dom.max_domains" class="submit">
        <van-button round block type="primary" :loading="domBusy" @click="addDomain">{{ t('reseller.domainAdd') }}</van-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.h {
  margin: 12px 16px 0;
  font-size: 14px;
}
.logo-pick {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  cursor: pointer;
}
.logo-pick img {
  border-radius: 8px;
  object-fit: cover;
}
.hint {
  margin: 4px 16px;
  font-size: 13px;
  color: var(--van-text-color-2);
}
.dom-item {
  padding: 8px 16px;
}
.dom-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.rec {
  margin: 6px 0;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.dom-btn {
  min-height: 44px;
}
.dom-actions {
  display: flex;
  gap: 8px;
  min-height: 44px;
  align-items: center;
}
.submit {
  padding: 16px;
}
</style>

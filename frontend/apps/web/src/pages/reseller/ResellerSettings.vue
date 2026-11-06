<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { amountToMinor, fromMinor, goBack, percentToPermille, permilleToPercent, safeAssetUrl, type MarkupKind } from '@24goods/shared'
import { api } from '../../api'
import { useResellerStore } from '../../stores/reseller'
import { showSuccessToast, toastError } from '../../ui'

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
const uploading = ref(false)
const parsed = computed(() => (kind.value === 'percent' ? percentToPermille(val.value) : amountToMinor(val.value)))
const logoSrc = computed(() => safeAssetUrl(logo.value))

onMounted(async () => {
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
  if (!f || uploading.value) return
  uploading.value = true
  try {
    // existing hostile-upload pipeline (magic bytes, re-encode); the result is our own /media/ URL
    logo.value = (await api.upload(f, { kind: 'image', filename: f.name })).url
  } catch (e) {
    toastError(e)
  } finally {
    uploading.value = false
    ;(ev.target as HTMLInputElement).value = ''
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
.submit {
  padding: 16px;
}
</style>

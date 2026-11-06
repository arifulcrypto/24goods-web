<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { BRAND, cleanStoreSlug, goBack, storeHostPreview } from '@24goods/shared'
import { api } from '../../api'
import TurnstileBox from '../../components/TurnstileBox.vue'
import { useTurnstile } from '../../composables/useTurnstile'
import { useSiteStore } from '../../stores/site'
import { minorMoney, useResellerStore } from '../../stores/reseller'
import { toastError, showSuccessToast } from '../../ui'

const { t, locale } = useI18n()
const router = useRouter()
const site = useSiteStore()
const store = useResellerStore()
const ts = useTurnstile('reseller_apply')
const slug = ref('')
const name = ref('')
const contact = ref('')
const busy = ref(false)
const base = computed(() => site.site?.resellers?.base_domain || BRAND.domain)
const preview = computed(() => storeHostPreview(slug.value, base.value))
const fee = computed(() => site.site?.resellers?.signup_fee_minor)

async function submit() {
  if (busy.value) return
  busy.value = true
  try {
    const token = ts.require()
    const r = await api.reseller.apply({
      slug: slug.value.trim().toLowerCase(),
      store_name: name.value.trim(),
      ...(contact.value.trim() ? { contact: contact.value.trim() } : {}),
      ...(token ? { turnstile_token: token } : {}),
    })
    await store.load(true)
    const no = r.order_no ?? r.order?.order_no ?? store.me?.signup_order_no
    if (no) {
      // sign-up fee: continue on the normal order pay flow
      await router.replace(`/orders/${encodeURIComponent(no)}`)
    } else {
      showSuccessToast(t('reseller.applied'))
      await router.replace('/reseller')
    }
  } catch (e) {
    toastError(e)
  } finally {
    ts.reset()
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('reseller.applyTitle')" left-arrow fixed placeholder safe-area-inset-top class="dock-top" @click-left="goBack(router, '/me')" />
    <section class="section card pad">
      <p class="muted">{{ t('reseller.applyIntro') }}</p>
      <p v-if="fee" class="fee">{{ t('reseller.signupFee', { fee: minorMoney(fee, 'CNY', locale) }) }}</p>
    </section>
    <van-form class="section card" @submit="submit">
      <van-field
        v-model="slug"
        name="slug"
        autocapitalize="none"
        autocomplete="off"
        maxlength="32"
        :label="t('reseller.slug')"
        :placeholder="t('reseller.slugPlaceholder')"
        :rules="[{ validator: (v: string) => !!cleanStoreSlug(v), message: t('errors.slug_invalid') }]"
      />
      <div class="preview muted">{{ t('reseller.yourAddress') }} <strong>{{ preview }}</strong></div>
      <van-field
        v-model="name"
        name="store_name"
        maxlength="64"
        :label="t('reseller.storeName')"
        :rules="[{ validator: (v: string) => v.trim().length > 0, message: t('common.required') }]"
      />
      <van-field v-model="contact" name="contact" maxlength="200" :label="t('reseller.contact')" :placeholder="t('reseller.contactPlaceholder')" />
      <TurnstileBox :ts="ts" class="ts" />
      <div class="submit">
        <van-button round block type="primary" native-type="submit" :loading="busy">{{ t('reseller.applyCta') }}</van-button>
      </div>
    </van-form>
  </div>
</template>

<style scoped>
.pad {
  padding: 12px 16px;
}
.fee {
  font-weight: 700;
  margin: 0;
}
.preview {
  padding: 4px 16px 8px;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.ts {
  padding: 0 16px;
}
.submit {
  padding: 16px;
}
</style>

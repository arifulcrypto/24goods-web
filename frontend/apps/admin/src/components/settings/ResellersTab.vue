<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiV5 } from '../../api'
import type { AdminSiteV5 } from '../../api-v5'
import { minorToYuan, permilleToPct, pctToPermille, yuanToMinor } from '../../lib/money'
import { useSave } from '../../composables/useSave'
import MarkupInput from '../MarkupInput.vue'
import SecretInput from '../SecretInput.vue'
import { secretChange, secretFromServer, type SecretField } from '../../lib/secret'

const props = defineProps<{ site: AdminSiteV5 }>()
const emit = defineEmits<{ saved: [s: AdminSiteV5] }>()
const { t } = useI18n()
const { saving, run } = useSave()
const errors = ref<Record<string, string>>({})
const f = reactive({
  base_domain: 'kesiv.com', link_mode: 'query' as 'query' | 'subdomain', fee: '99.00', kind: 'percent' as 'percent' | 'fixed', value: 100, max_pct: 200,
  max_fixed: '10000.00', min_payout: '10.00', settle: 72, reserved: [] as string[],
  cd_enabled: false, zone: '', origin: 'stores.kesiv.com', max_domains: 1,
})
const cfToken = ref<SecretField>(secretFromServer())
watch(
  () => props.site.resellers,
  (r) => {
    if (!r) return
    f.base_domain = r.base_domain
    f.link_mode = r.link_mode ?? 'query'
    f.fee = minorToYuan(r.signup_fee_minor)
    f.kind = r.default_markup_kind
    f.value = r.default_markup_value
    f.max_pct = permilleToPct(r.max_markup_permille)
    f.max_fixed = minorToYuan(r.max_markup_fixed_minor)
    f.min_payout = minorToYuan(r.min_payout_minor)
    f.settle = r.settle_after_hours
    f.reserved = [...r.reserved_slugs]
    f.cd_enabled = r.custom_domains_enabled
    f.zone = r.cf_zone_id
    f.origin = r.fallback_origin
    f.max_domains = r.max_domains
    cfToken.value = secretFromServer(props.site.secrets?.cloudflare_saas_token)
  },
  { immediate: true },
)
async function save() {
  const e: Record<string, string> = {}
  const fee = yuanToMinor(f.fee)
  const mf = yuanToMinor(f.max_fixed)
  const mp = yuanToMinor(f.min_payout)
  if (!/^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$/i.test(f.base_domain.trim())) e.domain = t('v5.resellers.domainInvalid')
  if (fee === null) e.fee = t('v5.amountInvalid')
  if (mf === null) e.mf = t('v5.amountInvalid')
  if (mp === null) e.mp = t('v5.amountInvalid')
  errors.value = e
  if (Object.keys(e).length) return
  const r = await run(async () => {
    const ch = secretChange(cfToken.value)
    if (ch) await apiV5.site.setSecret('cloudflare_saas_token', ch.value)
    return apiV5.site.put({
      resellers: {
        base_domain: f.base_domain.trim().toLowerCase(),
        link_mode: f.link_mode,
        signup_fee_minor: fee!,
        default_markup_kind: f.kind,
        default_markup_value: f.value,
        max_markup_permille: pctToPermille(f.max_pct),
        max_markup_fixed_minor: mf!,
        min_payout_minor: mp!,
        settle_after_hours: f.settle,
        reserved_slugs: f.reserved.map((s) => s.trim().toLowerCase()).filter(Boolean),
        custom_domains_enabled: f.cd_enabled,
        cf_zone_id: f.zone.trim(),
        fallback_origin: f.origin.trim().toLowerCase(),
        max_domains: f.max_domains,
      },
    })
  })
  if (r) emit('saved', r)
}
</script>

<template>
  <el-form label-position="top" class="wrap" @submit.prevent="save">
    <section class="panel block">
      <h2 class="h2">{{ t('v5.resellers.title') }}</h2>
      <p class="muted small">{{ t('v5.resellers.note') }}</p>
      <el-alert v-if="site.features && !site.features.resellers" type="warning" :closable="false" show-icon :title="t('v5.resellers.featureOff')" class="mb" />
      <el-form-item :label="t('v5.resellers.baseDomain')" :error="errors.domain">
        <el-input v-model="f.base_domain" maxlength="253" spellcheck="false" />
        <div class="muted small">{{ t('v5.resellers.baseDomainHint', { d: f.base_domain || 'kesiv.com' }) }}</div>
      </el-form-item>
      <el-form-item :label="t('v5.resellers.linkMode')">
        <el-select v-model="f.link_mode" style="width: 260px">
          <el-option value="query" :label="t('v5.resellers.linkQuery')" />
          <el-option value="subdomain" :label="t('v5.resellers.linkSub')" />
        </el-select>
        <div class="muted small">{{ t('v5.resellers.linkModeHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('v5.resellers.signupFee')" :error="errors.fee">
        <el-input v-model="f.fee" inputmode="decimal" style="width: 160px"><template #prefix>¥</template></el-input>
        <div class="muted small full">{{ t('v5.resellers.signupFeeHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('v5.resellers.defaultMarkup')"><MarkupInput v-model:kind="f.kind" v-model:value="f.value" /></el-form-item>
      <div class="grid">
        <el-form-item :label="t('v5.resellers.maxPct')"><el-input-number v-model="f.max_pct" :min="0" :max="1000" :precision="1" /><span class="unit muted">%</span></el-form-item>
        <el-form-item :label="t('v5.resellers.maxFixed')" :error="errors.mf"><el-input v-model="f.max_fixed" inputmode="decimal"><template #prefix>¥</template></el-input></el-form-item>
        <el-form-item :label="t('v5.resellers.minPayout')" :error="errors.mp"><el-input v-model="f.min_payout" inputmode="decimal"><template #prefix>¥</template></el-input></el-form-item>
        <el-form-item :label="t('v5.resellers.settle')"><el-input-number v-model="f.settle" :min="0" :max="8760" /><span class="unit muted">{{ t('site.hours') }}</span></el-form-item>
      </div>
      <el-form-item :label="t('v5.resellers.reserved')">
        <el-select v-model="f.reserved" multiple filterable allow-create default-first-option style="width: 100%" />
      </el-form-item>
    </section>
    <section class="panel block">
      <h2 class="h2">{{ t('v5.resellers.cdTitle') }}</h2>
      <p class="muted small">{{ t('v5.resellers.cdNote') }}</p>
      <el-form-item :label="t('v5.resellers.cdEnabled')"><el-switch v-model="f.cd_enabled" /></el-form-item>
      <el-form-item :label="t('v5.resellers.cdZone')"><el-input v-model="f.zone" maxlength="64" spellcheck="false" /></el-form-item>
      <el-form-item :label="t('v5.resellers.cdOrigin')"><el-input v-model="f.origin" maxlength="253" spellcheck="false" /></el-form-item>
      <el-form-item :label="t('v5.resellers.cdMax')"><el-input-number v-model="f.max_domains" :min="0" :max="10" /></el-form-item>
      <SecretInput v-model="cfToken" :label="t('v5.resellers.cdToken')" />
    </section>
    <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
  </el-form>
</template>

<style scoped src="./settings.css"></style>
<style scoped>
.mb {
  margin-bottom: 12px;
}
.full {
  width: 100%;
}
</style>

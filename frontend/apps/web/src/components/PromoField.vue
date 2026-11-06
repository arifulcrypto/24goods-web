<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { cleanPromoCode, isApiError, promoErrorKey } from '@24goods/shared'
import { api } from '../api'
import TurnstileBox from './TurnstileBox.vue'
import { useTurnstile } from '../composables/useTurnstile'
import { useSiteStore } from '../stores/site'

export interface AppliedPromo {
  code: string
  discount_minor: number
  /** The items changed after the preview and Turnstile forbids a silent re-check: the server prices it at checkout. */
  stale: boolean
}

/**
 * Promo code cell: input + Apply (POST /promo/validate = preview only). Hidden when
 * features.promo_codes is off or a reseller storefront is active (review-v5 §6/§7).
 */
const props = defineProps<{ items: { product_id: string; qty: number }[] }>()
const applied = defineModel<AppliedPromo | null>({ default: null })
const { t } = useI18n()
const site = useSiteStore()
const ts = useTurnstile('promo_validate')
const open = ref(false)
const input = ref('')
const busy = ref(false)
const error = ref('')

const itemsKey = computed(() => props.items.map((i) => `${i.product_id}:${i.qty}`).join(','))

async function validate(code: string, silent: boolean): Promise<boolean> {
  busy.value = true
  error.value = ''
  try {
    const token = silent ? undefined : ts.require()
    const r = await api.promoValidate({ code, items: props.items }, token)
    applied.value = { code: r.code, discount_minor: r.discount_minor, stale: false }
    return true
  } catch (e) {
    const c = isApiError(e) ? e.code : 'promo_invalid'
    if (silent) {
      applied.value = null
    } else {
      error.value = t(promoErrorKey(c))
    }
    return false
  } finally {
    busy.value = false
    if (!silent && ts.needed.value) ts.reset()
  }
}
async function apply() {
  const code = cleanPromoCode(input.value)
  if (!code) {
    error.value = t(promoErrorKey('promo_invalid'))
    return
  }
  if (!props.items.length || busy.value) return
  if (await validate(code, false)) open.value = false
}
function clear() {
  applied.value = null
  input.value = ''
  error.value = ''
}

watch(itemsKey, () => {
  const a = applied.value
  if (!a) return
  if (!props.items.length) return clear()
  if (ts.needed.value) applied.value = { ...a, stale: true }
  else void validate(a.code, true)
})

const label = computed(() => (applied.value ? applied.value.code : t('promo.cell')))
</script>

<template>
  <div v-if="site.showPromo" class="promo">
    <van-cell :title="t('promo.cell')" :value="applied ? applied.code : t('promo.enter')" is-link center @click="open = !open" />
    <div v-if="applied && !open" class="applied">
      <span class="ok">{{ t('promo.applied', { code: label }) }}</span>
      <button type="button" class="lnk" @click="clear">{{ t('promo.remove') }}</button>
    </div>
    <div v-if="open" class="edit">
      <div class="row">
        <van-field
          v-model="input"
          class="fld"
          autocapitalize="characters"
          autocomplete="off"
          maxlength="32"
          :placeholder="t('promo.placeholder')"
          :error-message="error"
          @keyup.enter="apply"
        />
        <van-button round type="primary" size="small" class="btn" :loading="busy" @click="apply">{{ t('promo.apply') }}</van-button>
      </div>
      <TurnstileBox :ts="ts" />
    </div>
  </div>
</template>

<style scoped>
.applied {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 16px;
  font-size: 13px;
}
.ok {
  color: var(--c-success, #07a35a);
}
.lnk {
  min-height: 44px;
  padding: 0 4px;
  border: 0;
  background: none;
  color: var(--c-price);
  font: inherit;
  cursor: pointer;
}
.edit {
  padding: 4px 8px 8px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.fld {
  flex: 1;
  min-width: 0;
}
.btn {
  flex: none;
  min-width: 72px;
  min-height: 44px;
}
</style>

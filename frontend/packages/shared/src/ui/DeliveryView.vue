<script setup lang="ts">
/**
 * Delivery presentation (review-v3 §5/§6): revealed cards split into labelled, copyable fields,
 * manual delivery text, "How to use" box, after-sales deadline and auto-confirm countdown.
 * Used by order detail, lookup and inline in chat. Revealed codes are held by the parent only.
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { DeliveryInfo } from '../types'
import { safeLinkUrl } from '../api/endpoints'
import { cardsToText, daysLeft, downloadText, isExpired, splitCard } from '../delivery'
import { formatCountdown, formatDateTime } from '../format'
import { copyText } from '../util'

const props = withDefaults(
  defineProps<{
    info?: DeliveryInfo | null
    /** Revealed codes (empty until revealed). */
    codes?: readonly string[]
    canReveal?: boolean
    revealing?: boolean
    manualText?: string | null
    orderNo: string
    afterSalesUntil?: string | null
    autoConfirmAt?: string | null
    /** Show the Confirm receipt button (status delivered, owner). */
    canConfirm?: boolean
    confirming?: boolean
    compact?: boolean
    revealLabel?: string
  }>(),
  { info: null, codes: () => [], manualText: null, afterSalesUntil: null, autoConfirmAt: null, revealLabel: '' },
)
const emit = defineEmits<{ reveal: []; hide: []; confirm: [] }>()
const { t, locale } = useI18n()

const now = ref(Date.now())
const timer = setInterval(() => (now.value = Date.now()), 1000)
onBeforeUnmount(() => clearInterval(timer))

const cards = computed(() => props.codes.map((c) => splitCard(c, props.info)))
const redeemUrl = computed(() => safeLinkUrl(props.info?.redeem_url))
const usage = computed(() => props.info?.usage_html || '')
const hasContent = computed(() => props.codes.length > 0 || !!props.manualText)
const showHow = computed(() => hasContent.value && (!!redeemUrl.value || !!usage.value))

const days = computed(() => daysLeft(props.afterSalesUntil, now.value))
const afterSalesText = computed(() => {
  if (!props.afterSalesUntil) return ''
  if (isExpired(props.afterSalesUntil, now.value)) return t('delivery.afterSalesEnded')
  const date = formatDateTime(props.afterSalesUntil, locale.value)
  return days.value && days.value > 1
    ? t('delivery.afterSalesUntil', { date, days: days.value })
    : t('delivery.afterSalesLastDay', { date })
})
const confirmIn = computed(() => (props.autoConfirmAt ? formatCountdown(props.autoConfirmAt, now.value) : null))

const copied = ref('')
let copiedTimer: ReturnType<typeof setTimeout> | undefined
async function copy(key: string, text: string) {
  if (await copyText(text)) {
    copied.value = key
    clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => (copied.value = ''), 1500)
  }
}
onBeforeUnmount(() => clearTimeout(copiedTimer))

function fieldLabel(f: { label: string; extra: boolean; index: number }): string {
  return f.label && !f.extra ? f.label : f.extra || f.index > 0 ? t('delivery.field', { n: f.index + 1 }) : ''
}
function download() {
  downloadText(`${props.orderNo || 'codes'}.txt`, cardsToText(props.codes))
}
</script>

<template>
  <div class="dv" :class="{ compact }">
    <!-- auto delivery: codes -->
    <section v-if="canReveal || codes.length" class="dv-block">
      <div class="dv-head">
        <h3>{{ t('delivery.codes') }}</h3>
        <button v-if="codes.length" type="button" class="dv-btn ghost" @click="emit('hide')">{{ t('delivery.hide') }}</button>
      </div>
      <template v-if="codes.length">
        <p v-if="!compact" class="dv-muted">{{ t('delivery.codesHint') }}</p>
        <div v-for="(fields, ci) in cards" :key="ci" class="dv-card">
          <div v-if="cards.length > 1" class="dv-card-n">{{ t('delivery.card', { n: ci + 1 }) }}</div>
          <div v-for="f in fields" :key="f.index" class="dv-field">
            <div class="dv-field-main">
              <span v-if="fieldLabel(f)" class="dv-label">{{ fieldLabel(f) }}</span>
              <code class="dv-code">{{ f.value }}</code>
            </div>
            <button
              type="button"
              class="dv-btn"
              :aria-label="t('delivery.copyField', { label: fieldLabel(f) || t('delivery.codes') })"
              @click="copy(`${ci}-${f.index}`, f.value)"
            >
              {{ copied === `${ci}-${f.index}` ? t('common.copied') : t('common.copy') }}
            </button>
          </div>
        </div>
        <div class="dv-actions">
          <button type="button" class="dv-btn wide" @click="copy('all', cardsToText(codes))">
            {{ copied === 'all' ? t('common.copied') : t('delivery.copyAll') }}
          </button>
          <button type="button" class="dv-btn wide" @click="download">{{ t('delivery.download') }}</button>
        </div>
      </template>
      <template v-else>
        <p class="dv-warn">{{ t('delivery.revealWarn') }}</p>
        <button type="button" class="dv-btn primary wide" :disabled="revealing" @click="emit('reveal')">
          {{ revealing ? t('common.loading') : revealLabel || t('delivery.reveal') }}
        </button>
      </template>
    </section>

    <!-- manual delivery -->
    <section v-if="manualText" class="dv-block">
      <div class="dv-head">
        <h3>{{ t('delivery.manual') }}</h3>
        <button type="button" class="dv-btn" @click="copy('manual', manualText!)">
          {{ copied === 'manual' ? t('common.copied') : t('delivery.copyAsText') }}
        </button>
      </div>
      <p class="dv-manual">{{ manualText }}</p>
    </section>

    <section v-if="showHow" class="dv-block dv-how">
      <h3>{{ t('delivery.howToUse') }}</h3>
      <a v-if="redeemUrl" class="dv-btn primary wide" :href="redeemUrl" target="_blank" rel="noopener noreferrer">{{
        t('delivery.redeem')
      }}</a>
      <!-- usage_html is sanitised server-side (markdown-it + nh3); only *_html fields use v-html -->
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div v-if="usage" class="dv-usage" v-html="usage" />
    </section>

    <p v-if="afterSalesUntil && hasContent" class="dv-after" :class="{ ended: isExpired(afterSalesUntil, now) }">
      {{ afterSalesText }}
    </p>
    <div v-if="confirmIn || canConfirm" class="dv-confirm">
      <span v-if="confirmIn" class="dv-muted">{{ t('delivery.autoConfirm', { time: confirmIn }) }}</span>
      <button v-if="canConfirm" type="button" class="dv-btn primary" :disabled="confirming" @click="emit('confirm')">
        {{ t('delivery.confirmReceipt') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.dv {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
  color: var(--c-text, #1f1f1f);
}
.dv-block h3 {
  font-size: 15px;
  margin: 0;
}
.dv-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  min-height: 44px;
}
.dv-muted {
  color: var(--c-muted, #6b6b6b);
  font-size: 12px;
  margin: 0 0 8px;
}
.dv-warn {
  background: #fff4e5;
  color: #8a4b00;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 18px;
  margin: 0 0 10px;
}
.dv-card {
  border: 1px dashed var(--c-code-border, #e7d9a8);
  background: var(--c-code-bg, #faf8f2);
  border-radius: 10px;
  padding: 4px 4px 4px 12px;
  margin-bottom: 8px;
}
.dv-card-n {
  font-size: 12px;
  color: var(--c-muted, #6b6b6b);
  padding-top: 6px;
}
.dv-field {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
}
.dv-field + .dv-field {
  border-top: 1px solid var(--c-line, #ececec);
}
.dv-field-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.dv-label {
  font-size: 11px;
  color: var(--c-muted, #6b6b6b);
}
.dv-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 15px;
  overflow-wrap: anywhere;
  user-select: all;
}
.dv-actions {
  display: flex;
  gap: 8px;
}
.dv-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  min-width: 44px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1px solid var(--c-line, #d9d9d9);
  background: var(--c-surface, #fff);
  color: inherit;
  font: inherit;
  font-size: 13px;
  text-decoration: none;
  cursor: pointer;
  white-space: nowrap;
}
.dv-btn.wide {
  flex: 1;
  width: 100%;
}
.dv-btn.primary {
  background: var(--c-accent, #ffd60a);
  border-color: var(--c-accent, #ffd60a);
  color: var(--c-on-accent, #1f1f1f);
  font-weight: 700;
}
.dv-btn.ghost {
  border-color: transparent;
  background: none;
  color: var(--c-muted, #6b6b6b);
}
.dv-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.dv-manual {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
  background: var(--c-code-bg, #faf8f2);
  border-radius: 10px;
  padding: 10px 12px;
}
.dv-how {
  background: var(--c-accent-soft, #fff6cc);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.dv-usage {
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.dv-usage :deep(a) {
  color: inherit;
  text-decoration: underline;
}
.dv-usage :deep(p),
.dv-usage :deep(ul),
.dv-usage :deep(ol) {
  margin: 0 0 6px;
}
.dv-after {
  margin: 0;
  font-size: 12px;
  color: var(--c-accent-ink, #7a4f00);
}
.dv-after.ended {
  color: var(--c-muted, #6b6b6b);
}
.dv-confirm {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.compact .dv-head {
  min-height: 36px;
}
</style>
